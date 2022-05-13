Bootstrap: docker

From: python:3

%labels
    Topic VariantEffect

%post
    #This is based on debian buster...
    apt-get update --fix-missing && apt-get install -y python3-pip git openjdk-11-jdk
    cd /
    git clone --depth=50 --branch=master https://github.com/afonsoguerra/SnpEffWrapper.git sanger-pathogens/SnpEffWrapper
    cd sanger-pathogens/SnpEffWrapper
    bash install_dependencies.sh
    rm -rf /sanger-pathogens/SnpEffWrapper/build/clinEff/
    rm -rf /sanger-pathogens/SnpEffWrapper/build/*.zip
    python3 -m pip install setuptools==58
    python3 -m pip install pyvcf3
    python3 -m pip install /sanger-pathogens/SnpEffWrapper

%runscript
    #export SNPEFF_EXEC=/sanger-pathogens/SnpEffWrapper/build/snpEff_v4_1l_core/snpEff.jar
    #exec snpEffBuildAndRun --snpeff-exec /sanger-pathogens/SnpEffWrapper/build/snpEff_v4_1l_core/snpEff.jar --java-exec /usr/bin/java "$@"
    export SNPEFF_EXEC=/sanger-pathogens/SnpEffWrapper/build/snpEff_v4_3t_core/snpEff.jar
    exec snpEffBuildAndRun --snpeff-exec /sanger-pathogens/SnpEffWrapper/build/snpEff_v4_3t_core/snpEff.jar --java-exec /usr/bin/java "$@"

