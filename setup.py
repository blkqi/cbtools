from setuptools import setup

setup(name='cbtools',
      version='0.0.0',
      package_dir={'': 'src'},
      package_data={'cbtools': ['*.xsd', '*.gql']},
      packages=['cbtools'],
      scripts=['bin/cbinfo', 'bin/cbrename', 'bin/cbtag', 'bin/cbscale'],
      install_requires=['lxml', 'jmespath', 'requests', 'dictdiffer'],
      license_files=['LICENSE'],)
