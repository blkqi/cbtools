import typing
import tempfile
import multiprocessing

from pathlib import Path
from cbtools import image
from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.functools import partial, chain
from cbtools.constants import *


# TODO move create / extract functionality to core

def _flatten_name(name):
    # TODO just rename the file completely with enumeration
    return Path(name).as_posix().replace('/', '__')


def _extract_all(cbx_path, src_path, flat=False):
    logger.info(f'Unpacking archive "{cbx_path}"')

    cfile = ComicArchive(cbx_path)

    for member in cfile.list():
        if flat:
            if member.is_dir():
                continue

            path = (src_path / _flatten_name(member.name))
        else:
            path = (src_path / member.name)

        with open(path, 'wb') as f:
            cfile.extract(member.name, f)


def _create_archive(out_path, src_path, dst_path):
    logger.info(f'Packing archive "{out_path}"')

    add_paths = dst_path.iterdir()
    info_path = (src_path / COMICINFO_XML_NAME)
    if info_path.exists():
        add_paths = chain([info_path], add_paths)

    ComicArchive(out_path).create(*add_paths)

    logger.info(f'Created archive "{out_path}"')


_output_suffix = 'cbconvert'

def _output_filename(path, root=None):
    stem = '_'.join((path.stem, _output_suffix))
    return ((root or path.parent) / (str(stem) + '.cbz'))


def _convert_images(src_path, dst_path):
    pool = multiprocessing.Pool(16)
    paths = (p for p in src_path.iterdir() if p.name != COMICINFO_XML_NAME)
    result = pool.map(partial(image.convert, root=dst_path), paths)


def convert(files, root, **kwds):
    # TODO upscale images

    for cbx_path in expand_paths(files):
        with tempfile.TemporaryDirectory() as tmp_dir:

            out_path = _output_filename(cbx_path, root=root)

            if out_path.exists():
                logger.error(f'{out_path!s}: already exists')
                return 1

            tmp_path = Path(tmp_dir)
            (src_path := (tmp_path / 'extract')).mkdir()
            (dst_path := (tmp_path / 'convert')).mkdir()

            _extract_all(cbx_path, src_path, flat=True)
            _convert_images(src_path, dst_path)
            _create_archive(out_path, src_path, dst_path)
