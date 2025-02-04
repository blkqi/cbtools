from setuptools import setup

setup(name='cbtools',
      version='0.0.0',
      package_dir={'': 'src'},
      package_data={'cbtools': ['*.xsd'], 'cbtools.tag': ['*.gql']},
      packages=['cbtools',
                'cbtools.config',
                'cbtools.core',
                'cbtools.core.constants',
                'cbtools.manager',
                'cbtools.manager.api',
                'cbtools.manager.queue',
                'cbtools.rename',
                'cbtools.tag',
                'cbtools.tag.extensions'],
      scripts=['bin/cbinfo', 'bin/cbrename', 'bin/cbtag', 'bin/cbscale', 'bin/cbmanager'],
      install_requires=['lxml', 'jmespath', 'requests', 'dictdiffer', 'waitress', 'flask', 'watchdog'],
      license_files=['LICENSE'],)
