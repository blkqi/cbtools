import json

from cbtools.core import ComicArchive, expand_paths


def info(files):
    files = expand_paths(files)
    for path in files:
        cfile = ComicArchive(path)
        print(json.dumps(cfile.info(), indent=2))
