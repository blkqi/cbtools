import typing
import tempfile
import itertools

from pathlib import Path
from cbtools import image
from cbtools.log import logger
from cbtools.core import ComicArchive, ComicInfo
from cbtools.constants import *


def _write_output(outpath, srcpath, dstpath):
    logger.info(f'Packing archive "{outpath}"')

    cfile = ComicArchive(outpath)

    add_paths = sorted(dstpath.iterdir())
    info_path = srcpath / COMICINFO_XML_NAME

    if info_path.exists():
        add_paths = itertools.chain([info_path], add_paths)

    for path in add_paths:
        with open(path, 'rb') as f:
            cfile.add(path.name, f)

    logger.info(f'Created archive "{outpath}"')


def _convert_images(srcpath, dstpath, profile, flags):
    for path in sorted(srcpath.iterdir()):
        target = (dstpath / path.name)
        try:
            image.convertImage(str(path), str(target), 'Kindle Scribe', flags)
        except RuntimeError as e:
            logger.warning(f'Cannot read image file {path}')
            pass
        # TODO auto contrast ?


def _extract_flatten(cfile, root):
    logger.info(f'Unpacking archive "{cfile.filepath}"')

    for member in cfile.list():
        if member.is_dir():
            continue

        name = Path(member.name).as_posix().replace('/', '__')
        path = (root / name)

        with open(path, 'wb') as f:
            cfile.extract(member.name, f)


_output_suffix = 'cbconvert'

def _output_filename(path, root=None):
    stem = '_'.join((path.stem, _output_suffix))
    return ((root or path.parent) / (str(stem) + '.cbz'))


def convert(path, **kwds):
    cfile = ComicArchive(path)

    ## image processing options
    flags = 0
    flags |= image.ImageFlags.Resize
    #flags |= image.ImageFlags.Fill
    #flags |= image.ImageFlags.Quantize

    ## device profile
    profile = 'Kindle Scribe'

    with tempfile.TemporaryDirectory() as srcdir, \
            tempfile.TemporaryDirectory() as dstdir:

        src_path = Path(srcdir)
        dst_path = Path(dstdir)
        out_path = _output_filename(cfile.filepath, root=path.parent)

        if out_path.exists():
            raise RuntimeError(f'path "{out_path}" already exists!')

        _extract_flatten(cfile, src_path)
        _convert_images(srcpath, dst_path, profile, flags)
        _write_output(out_path, src_path, dst_path)
