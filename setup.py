import os
from setuptools import setup, find_packages
import multiprocessing

setup(name='snpEffWrapper',
      version='0.2.6',
      scripts=[
        'scripts/snpEffBuildAndRun'
      ],
      install_requires=[
        'setuptools==58',
        'Jinja2',
        'PyVCF3',
        'PyYAML'
      ],
      include_package_data=True,
      package_data={
        'data': ['snpEffWrapper/data/*'],
        'test_data': ['snpEffWrapper/tests/data/*']
      },
      packages=find_packages(),
      zip_safe=False
)
