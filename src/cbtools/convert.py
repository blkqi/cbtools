import os
import typing
import tempfile
import subprocess
import multiprocessing

from pathlib import Path
from cbtools import image
from cbtools.log import logger
from cbtools.core import ComicArchive, expand_paths
from cbtools.config import config
from cbtools.functools import partial, chain
from cbtools.constants import *


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


def _upscale_images(src_path, dst_path, scale, noise, ftype, gpuid):
    logger.info(f'Upscaling image data')

    cmd = [WAIFU2X_BIN,
           '-i', str(src_path), '-o', str(dst_path),
           '-s', str(scale), '-n', str(noise),
           '-f', str(ftype), '-g', str(gpuid), '-m', WAIFU2X_MODEL]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        raise RuntimeError(f'waifu2x-ncnn-vulkan not found: {entrypoint}')

    if proc.returncode != 0:
        sys.stderr.write(proc.stdout.decode())
        raise RuntimeError(f'waifu2x-ncnn-vulkan error code: {proc.returncode}')


def _convert_images(src_path, dst_path):
    logger.info(f'Converting image data')

    pool = multiprocessing.Pool(config['image.jobs'])
    paths = (p for p in src_path.iterdir() if p.name != COMICINFO_XML_NAME)
    result = pool.map(partial(image.convert, root=dst_path), paths)


def convert(files, root, **kwds):
    # TODO Retrieve from config module
    opts = {
        'scale': 2,
        'noise': 2,
        'ftype': 'jpg',
        'gpuid': 'auto',
    }

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
            _upscale_images(ext_path, ups_path, **opts)
            _convert_images(ups_path, cnv_path)
            _create_archive(out_path, ext_path, cnv_path)
