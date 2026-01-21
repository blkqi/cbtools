import os
import sys
import tempfile
import subprocess
import multiprocessing

from pathlib import Path
from cbtools import image
from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.functools import partial, chain
from cbtools.constants import COMICINFO_XML_NAME
from cbtools.exceptions import MissingDependencyError, SubprocessError


WAIFU2X_BIN = os.getenv('WAIFU2X_BIN', '/usr/bin/waifu2x-ncnn-vulkan')
WAIFU2X_MODEL = os.getenv('WAIFU2X_MODEL', '/usr/share/waifu2x-ncnn-vulkan/models-upconv_7_anime_style_art_rgb')


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

    add_paths = [str(dst_path) + '/*']
    info_path = (src_path / COMICINFO_XML_NAME)
    if info_path.exists():
        add_paths = chain([info_path], add_paths)

    ComicArchive(out_path).create(*add_paths)

    logger.info(f'Created archive "{out_path}"')


def _output_filename(path, root=None):
    stem = '_'.join((path.stem, config['convert.suffix']))
    return ((root or path.parent) / (str(stem) + path.suffix))


def _process_skips(src_path, dst_path):
    factor = config['image.upscale.factor']
    cutoff = config['image.upscale.cutoff']
    devdim = config['image.size']

    for path in src_path.iterdir():
        if path.name == COMICINFO_XML_NAME:
            continue

        imgdim = image.size(path)

        if not imgdim:
            logger.debug(f'skip: image not recognized: "{path.name}"')
            continue

        if (factor * imgdim[1]) > (cutoff * devdim[1]):
            logger.debug(f'skip: image dimension exceeds cutoff: "{path.name}"')
            path.rename(dst_path / path.name)


def _upscale_images(src_path, dst_path):
    logger.info('Upscaling image data')

    _process_skips(src_path, dst_path)

    cmd = [WAIFU2X_BIN,
           '-m', WAIFU2X_MODEL,
           '-i', str(src_path),
           '-o', str(dst_path),
           '-s', str(config['image.upscale.factor']),
           '-n', str(config['image.upscale.noise']),
           '-f', str(config['image.upscale.format']),
           '-g', str(config['image.upscale.gpu']),
           '-t', str(config['image.upscale.tile_size']),
           '-j', str(config['image.upscale.thread_count'])]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        raise MissingDependencyError(f'waifu2x-ncnn-vulkan not found: {WAIFU2X_BIN}')

    if proc.returncode != 0:
        sys.stderr.write(proc.stdout.decode())
        raise SubprocessError(f'waifu2x-ncnn-vulkan error code: {proc.returncode}')


def _convert_images(src_path, dst_path):
    logger.info('Converting image data')

    pool = multiprocessing.Pool(config['convert.jobs'])
    paths = (p for p in src_path.iterdir() if p.name != COMICINFO_XML_NAME)
    pool.map(partial(image.convert, root=dst_path), paths)


def convert(files, root, delete_source=False, **kwds):
    for cbx_path in expand_paths(files):
        with tempfile.TemporaryDirectory() as tmp_dir:

            out_path = _output_filename(cbx_path, root=root)

            if out_path.exists():
                logger.error(f'{out_path!s}: already exists')
                return 1

            tmp_path = Path(tmp_dir)
            (ext_path := (tmp_path / 'extract')).mkdir()
            (ups_path := (tmp_path / 'upscale')).mkdir()
            (cnv_path := (tmp_path / 'convert')).mkdir()

            _extract_all(cbx_path, ext_path, flat=True)
            _upscale_images(ext_path, ups_path)
            _convert_images(ups_path, cnv_path)
            _create_archive(out_path, ext_path, cnv_path)

        if delete_source:
            try:
                cbx_path.unlink()
                logger.info(f'Deleted source file "{cbx_path}"')
            except Exception as e:
                logger.warning(f'Failed to delete source file "{cbx_path}": {e}')
