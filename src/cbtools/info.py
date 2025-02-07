import json

from pathlib import Path

from cbtools.core import ComicArchive, expand_paths


def info(files: list[Path]) -> None:
    files = expand_paths(files)

    for path in files:
        cfile = ComicArchive(path)
        print(json.dumps(cfile.info(), indent=2))
