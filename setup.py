from setuptools import setup

packages=[
    'cbtools',
    'cbtools.core',
    'cbtools.manager',
    'cbtools.tag',
    'cbtools.tag.extensions'
]

setup(name='cbtools',
      version='0.0.0',
      package_dir={'': 'src'},
      package_data={'cbtools': ['*.xsd'], 'cbtools.tag': ['*.gql']},
      packages=packages,
      scripts=['bin/cb', 'bin/cbinfo', 'bin/cbrename', 'bin/cbtag', 'bin/cbscale', 'bin/cbmanager'],
      install_requires=['lxml', 'jmespath', 'requests', 'dictdiffer', 'waitress', 'flask', 'watchdog'],
      license_files=['LICENSE'],)
