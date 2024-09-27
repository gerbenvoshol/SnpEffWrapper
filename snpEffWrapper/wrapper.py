import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import vcf
import yaml

from collections import Counter
from jinja2 import Environment, PackageLoader
from subprocess import CalledProcessError

logger = logging.getLogger(__name__)


class MissingSNPEffError(ValueError):
    pass


class WrongJavaError(ValueError):
    pass


class NoCommonContigsError(ValueError):
    pass


class MissingCodonTableError(ValueError):
    pass


class UnknownCodingTableError(ValueError):
    pass


class BuildDatabaseError(ValueError):
    pass


class AnnotationError(ValueError):
    pass


def _java_version_ok(java):
    if java is None:
        return False
    try:
        output = subprocess.check_output([java, "-Xmx10m", "-version"], stderr=subprocess.STDOUT)
        first_line = output.decode("utf-8").splitlines()[0]
        match = re.match("^openjdk", first_line)
        return match is not None
    except CalledProcessError:  # Probably this java doesn't exist
        return False
    except IndexError:  # Didn't return any output
        return False


def _choose_java():
    possible_javas = [
        shutil.which("java"),
        "/software/bin/java",
        "/software/pathogen/external/apps/usr/local/jdk1.7.0_21/bin/java",
    ]
    for java in possible_javas:
        if _java_version_ok(java):
            logger.debug("Using '%s'", java)
            return java
    raise WrongJavaError("Could not find a suitable version of Java (1.7)")


def check_and_amend_executables(args):
    """Sets default executables and checks that they are suitable"""
    if args.snpeff_exec is not None:
        args.snpeff_exec = args.snpeff_exec.name
    elif os.path.isfile("snpEff.jar"):
        args.snpeff_exec = "snpEff.jar"
    elif not shutil.which("snpEff.jar") is None:
        args.snpeff_exec = shutil.which("snpEff.jar")
    else:
        raise MissingSNPEffError("Could not find snpEff.jar in PATH")

    if not os.path.isfile(args.snpeff_exec):
        raise MissingSNPEffError("Could not find '%s'" % args.snpeff_exec)

    if args.java_exec is None:
        args.java_exec = _choose_java()
    else:
        args.java_exec = args.java_exec.name
    if not _java_version_ok(args.java_exec):
        raise WrongJavaError(
            "Needs Java 1.7, %s isn't or couldn't be found" % args.java_exec
        )

    return args


def parse_coding_table(coding_table_str):
    logger.debug("Parsing the coding table")
    return yaml.full_load(coding_table_str)


def _is_gtf(file_name):
    """Check if the file is a GTF based on the extension"""
    return file_name.lower().endswith(".gtf") or file_name.endswith(".gtf.gz")


def get_annotation_contigs(annotation_file):
    """Hacky parser to get contigs from annotation file (GFF/GTF)

    Just looks for the contigs, assumes they're the first column
    of a tab delimited file where the line doesn't start with '#'
    """
    logger.debug("Getting the contigs from the annotation file")
    annotation_file.seek(0)
    contigs = set()
    for line in annotation_file:
        if line[0] == "#":
            continue
        contig = line.split("\t")[0].strip()
        contigs.add(contig)
    return sorted(contigs)


def get_vcf_contigs(vcf_file):
    """Hacky vcf parser to get contigs

    Just looks for the contigs, assumes they're the first column
    of a tab delimited file where the line doesn't start with '#'"""
    vcf_file.seek(0)
    contigs = set()
    for line in vcf_file:
        if line[0] == "#":
            continue
        contig = line.split("\t")[0].strip()
        contigs.add(contig)
    return sorted(contigs)


def check_contigs(vcf_contigs, annotation_contigs, coding_table):
    """Check that contigs are consistent

    If any contig in the VCF isn't in the coding table, fail.
    If not all of the VCF contigs are in the GTF/GFF annotation file, raise warnings.
    If none of the VCF contigs are in the annotation file, fail"""
    logger.info("Checking that the VCF and GTF/GFF contigs are consistent")

    # Check the VCF contigs are consistent with the coding table
    missing_coding_tables = []
    if "default" not in coding_table:
        missing_coding_tables = [
            table for table in vcf_contigs if table not in coding_table
        ]
    for table in missing_coding_tables:
        logger.warn("Cannot annotate VCF, no coding table set for '%s'" % table)

    # Check the VCF contigs are consistent with the annotation contigs
    missing_contigs = [contig for contig in vcf_contigs if contig not in annotation_contigs]
    for contig in missing_contigs:
        logger.warn("Could not annotate contig '%s', no annotation data" % contig)

    # Check the coding_table has known encodings
    known_encodings = [
        "Alternative_Flatworm_Mitochondrial",
        "Alternative_Yeast_Nuclear",
        "Ascidian_Mitochondrial",
        "Bacterial_and_Plant_Plastid",
        "Blepharisma_Macronuclear",
        "Chlorophycean_Mitochondrial",
        "Ciliate_Nuclear",
        "Coelenterate",
        "Dasycladacean_Nuclear",
        "Echinoderm_Mitochondrial",
        "Euplotid_Nuclear",
        "Flatworm_Mitochondrial",
        "Hexamita_Nuclear",
        "Invertebrate_Mitochondrial",
        "Mitochondrial",
        "Mold_Mitochondrial",
        "Mycoplasma",
        "Protozoan_Mitochondrial",
        "Scenedesmus_obliquus_Mitochondrial",
        "Spiroplasma",
        "Standard",
        "Thraustochytrium_Mitochondrial",
        "Trematode_Mitochondrial",
        "Vertebrate_Mitochondrial",
        "Yeast_Mitochondrial",
    ]
    unknown_encodings = [
        enc for enc in coding_table.values() if enc not in known_encodings
    ]
    for encoding in unknown_encodings:
        logger.warn("Could not find coding table '%s'" % encoding)

    # Blow up for critical issues
    if len(missing_coding_tables) > 0:
        raise MissingCodonTableError(
            "Could not find coding tables for all contigs, see warnings for details"
        )
    if missing_contigs == vcf_contigs:
        raise NoCommonContigsError(
            "Could not find annotation data for any contigs, see warnings for details"
        )
    if len(unknown_encodings) > 0:
        raise UnknownCodingTableError(
            "Could not find coding table, see warnings for details"
        )


def create_temp_database(annotation_file):
    """Create a temporary database directory for snpEff"""
    file_ext = "gtf" if _is_gtf(annotation_file.name) else "gff"
    database_dir = tempfile.mkdtemp(prefix="snpeff_data_dir_", dir=os.getcwd())
    logger.debug("Creating directory %s for temporary database" % database_dir)
    data_dir = os.path.join(database_dir, "data")
    logger.debug("data_dir: %s" % data_dir)
    os.makedirs(data_dir, mode=0o755)
    shutil.copy(annotation_file.name, os.path.join(data_dir, f"genes.{file_ext}"))
    return database_dir


def get_genome_name(gff_file):
    return re.sub("\.gff(\.gz)?$", "", gff_file.name)


def create_config_file(temp_database_dir, genome_name, vcf_contigs, coding_table):
    env = Environment(loader=PackageLoader("snpEffWrapper", "data"))
    template = env.get_template("config.template")
    output_filename = os.path.join(temp_database_dir, "config")
    config_content = template.render(
        temp_database_dir=temp_database_dir,
        genome_name=genome_name,
        vcf_contigs=vcf_contigs,
        coding_table=coding_table,
    )
    logger.debug("Writing config to %s" % output_filename)
    with open(output_filename, "w") as output_file:
        print(config_content, file=output_file, flush=True)
    return output_filename


def _snpeff_build_database(java_exec, snpeff_exec, config_filename, annotation_file, stdout, stderr):
    """Build the snpEff database, using -gff3 for GFF files and -gtf22 for GTF files"""
    annotation_flag = "-gtf22" if _is_gtf(annotation_file.name) else "-gff3"
    command = [
        java_exec,
        "-Xmx4g",
        "-jar",
        snpeff_exec,
        "build",
        annotation_flag,
        "-verbose",
        "data",
        "-c",
        config_filename,
    ]
    logger.info("Building snpeff database")
    logger.debug(
        "Using the following command: '%s'", " ".join([str(c) for c in command])
    )
    try:
        subprocess.check_call(command, stdout=stdout, stderr=stderr)
    except CalledProcessError:
        raise BuildDatabaseError("Problem building the database from your annotation file")


def _snpeff_annotate(java_exec, snpeff_exec, vcf_filename, config_filename, output_file, stderr, annotation_stats_file):
    command = [
        java_exec,
        "-Xmx4g",
        "-jar",
        snpeff_exec,
        "ann",
        "-nodownload",
        "-verbose",
        "-no-downstream",
        "-no-intergenic",
        "-no-intron",
        "-no-upstream",
        "-no-utr",
        "-csvStats",
        output_file.name + ".csv",
        "-stats",
        annotation_stats_file,
        "-c",
        config_filename,
        "data",
        vcf_filename,
    ]
    logger.info("Annotating %s" % vcf_filename)
    logger.debug("Using the following command: '%s'", " ".join(command))
    logger.debug("writing output to %s" % output_file.name)
    try:
        subprocess.check_call(command, stdout=output_file, stderr=stderr)
    except CalledProcessError:
        raise AnnotationError("Problem annotating %s" % vcf_filename)


def _get_snpeff_output_files(temp_database_dir, debug):
    temp_output_file = tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=temp_database_dir,
        prefix="snpeff_output_",
        suffix=".vcf")
    if debug:
        build_stdout, build_stderr = sys.stdout, sys.stderr
        annotate_stderr = sys.stderr
    else:
        build_stdout = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=temp_database_dir,
            prefix="snpeff_build_db_",
            suffix=".o")
        build_stderr = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=temp_database_dir,
            prefix="snpeff_build_db_",
            suffix=".e")
        annotate_stderr = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=temp_database_dir,
            prefix="snpeff_annotate_",
            suffix=".e")

    return temp_output_file, build_stdout, build_stderr, annotate_stderr


def run_snpeff(temp_database_dir, java_exec, snpeff_exec, vcf_file, config_filename, debug, annotation_file):
    temp_output_file, build_stdout, build_stderr, annotate_stderr = _get_snpeff_output_files(temp_database_dir, debug)
    vcf_filename = vcf_file.name
    _snpeff_build_database(
        java_exec,
        snpeff_exec,
        config_filename,
        annotation_file,
        build_stdout,
        build_stderr
    )
    logger.debug("Outputting temporary VCF to %s" % vcf_filename)
    annotation_stats_file = os.path.join(temp_database_dir, "snpEff_summary.html")
    _snpeff_annotate(
        java_exec,
        snpeff_exec,
        vcf_filename,
        config_filename,
        temp_output_file,
        annotate_stderr,
        annotation_stats_file
    )
    temp_output_file.close()
    temp_output_file = open(temp_output_file.name, "r")
    return temp_output_file


def _remove_headers(annotated_vcf):
    """Removes all headers from a VCF before parsing

    Some VCF headers (including INFO and FILTER headers) cannot be parsed
    by the current version of PyVCF.  In this case we are not interested
    in what they are so this removes them"""
    header_regex = re.compile("^##.*$")
    return (line for line in annotated_vcf if not header_regex.match(line))


def check_annotations(annotated_vcf):
    logger.info("Checking the annotated VCF for common issues")
    error_map = {
        "WARNING_REF_DOES_NOT_MATCH_GENOME": "The reference base in your VCF didn't match the base in the annotation file. Are you sure you have the right reference?",
        "WARNING_SEQUENCE_NOT_AVAILABLE": "A reference sequence was not available in your annotation file. Please check that a reference sequence is available for every contig in your VCF",
        "WARNING_TRANSCRIPT_NO_START_CODON": "Start codon does not match any 'start' codon in the CodonTable. This usually indicates an error on the reference genome (or database) but could be also due to a misconfigured codon table for the genome.",
        "ERROR_CHROMOSOME_NOT_FOUND": "A contig in your VCF could not be found in your annotation file. Are you sure that contigs use consitent names between your input data and the reference?",
        "ERROR_OUT_OF_CHROMOSOME_RANGE": "One of your variants appears to be in a position beyond the end of the reference sequence. That's really weird, please check that you reference sequence matches your input data",
    }
    error_counter = Counter()
    annotated_vcf.seek(0)
    modified_vcf = _remove_headers(
        annotated_vcf
    )  # FIXME: A future version of PyVCF may be able to parse nasty headers
    vcf_reader = vcf.Reader(modified_vcf)
    annotations = ""
    for record in vcf_reader:
        try:
            annotations = ",".join(record.INFO["ANN"])
        except KeyError:
            counter_update = {error: 1 for error in error_map if error in annotations}
            error_counter.update(counter_update)
    for error, count in error_counter.items():
        logger.warn("%s instances of '%s': %s" % (count, error, error_map[error]))
    if len(error_counter) > 0:
        logger.warn(
            "There were problems during the annotation, please review the warnings for details"
        )


def move_annotated_vcf(annotated_vcf, output_vcf):
    if output_vcf is sys.stdout:
        annotated_vcf.seek(0)
        logger.info("Writing output to stdout")
        print(annotated_vcf.read(), file=output_vcf)
        annotated_vcf.close()
    else:
        annotated_vcf.close()
        output_vcf.close()
        logger.info(
            "Moving annotated VCF from %s to %s" % (annotated_vcf.name, output_vcf.name)
        )
        shutil.move(annotated_vcf.name, output_vcf.name)


def move_summary_csv(summary_csv, output_csv):
    if output_csv is sys.stdout:
        logger.info("Now dumping CSV to stdout")
    else:
        summary_csv.close()
        output_csv.close()
        logger.info(
            "Moving summary CSV from %s to %s"
            % (summary_csv.name + ".csv", output_csv.name + ".csv")
        )
        shutil.move(summary_csv.name + ".csv", output_csv.name + ".csv")


def delete_temp_database(temp_database_dir):
    logger.debug("Deleting temporary files from %s" % temp_database_dir)
    shutil.rmtree(temp_database_dir)


def annotate_vcf(args):
    coding_table = parse_coding_table(args.coding_table)
    annotation_contigs = get_annotation_contigs(args.annotation_file)
    vcf_contigs = get_vcf_contigs(args.vcf_file)
    check_contigs(vcf_contigs, annotation_contigs, coding_table)
    temp_database_dir = create_temp_database(args.annotation_file)
    genome_name = get_genome_name(args.annotation_file)
    config_filename = create_config_file(
        temp_database_dir, genome_name, vcf_contigs, coding_table
    )
    annotated_vcf = run_snpeff(
        temp_database_dir,
        args.java_exec,
        args.snpeff_exec,
        args.vcf_file,
        config_filename,
        args.debug,
        args.annotation_file
    )
    check_annotations(annotated_vcf)
    move_annotated_vcf(annotated_vcf, args.output_vcf)
    move_summary_csv(annotated_vcf, args.output_vcf)
    if args.keep:
        logging.info("You can find the temporary files in '%s'", temp_database_dir)
    else:
        delete_temp_database(temp_database_dir)
