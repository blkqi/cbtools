from setuptools import setup

packages=[
    'cbtools',
    'cbtools.tag',
    'cbtools.tag.extensions',
    'cbtools.manager',
]

setup(name='cbtools',
      version='0.0.0',
      package_dir={'': 'src'},
      package_data={'cbtools': ['*.xsd'], 'cbtools.tag': ['*.gql']},
      packages=packages,
      scripts=['bin/cbinfo', 'bin/cbrename', 'bin/cbtag', 'bin/cbscale', 'bin/cbconvert', 'bin/cbmanager'],
      install_requires=['lxml', 'jmespath', 'requests', 'dictdiffer', 'waitress', 'flask', 'watchdog', 'Pillow>=6.2.2'],
      license_files=['LICENSE'],)
