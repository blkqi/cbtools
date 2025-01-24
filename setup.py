from setuptools import setup

setup(name='cbtools',
      version='0.0.0',
      package_dir={'': 'src'},
      packages=['cbtools'],
      scripts=['bin/cbinfo', 'bin/cbrename', 'bin/cbtag'],
      install_requires=['lxml'])
