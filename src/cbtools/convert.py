import typing
import tempfile
import itertools

from pathlib import Path
from cbtools.log import logger
from cbtools.core import ComicArchive, ComicInfo, expand_paths
from cbtools.constants import *


# TODO move image functions to image module

from PIL import Image, ImageOps, UnidentifiedImageError


def _image_is_spread(image):
    return image.size[0] > image.size[1]


def _image_padding_color(image):
    # TODO Determine fill color from image histogram
    return 'black'


def _image_padding_method(image, size):
    if not any((p >= q) for (p, q) in zip(image.size, size)):
        return Image.Resampling.LANCZOS
    return Image.Resampling.BICUBIC


def _image_gamma_table(gamma, gain):
    return [min(255, int((x / 255.) ** (1. / gamma) * gain * 255.)) for x in range(256)]


def _process_image_bands(image):
    return image.convert('L')


def _process_image_rotate(image):
    if _image_is_spread(image):
        return image.rotate(90, resample=Image.Resampling.BICUBIC, expand=True)
    return image


def _process_image_gamma(image, gamma=1.0, gain=1.0):
    assert(image.mode == 'L')
    assert(len(image.getbands()) == 1)
    return ImageOps.autocontrast(image.point(_image_gamma_table(gamma, gain)))


def _process_image_pad(image, size):
    method = _image_padding_method(image, size)
    color = _image_padding_color(image)
    return ImageOps.pad(image, size, method=method, color=color)


def _process_image(src_img, dst_img, **kwds):
    image = Image.open(src_img)
    image = _process_image_bands(image)
    image = _process_image_rotate(image)
    image = _process_image_gamma(image, kwds['gamma'])
    image = _process_image_pad(image, kwds['size'])
    image.save(dst_img, kwds['format'], optimize=kwds['optimize'], quality=kwds['quality'])


def _convert_images(src_cfile, dst_cfile, **kwds):
    for member in src_cfile.list():
        if any((member.name == COMICINFO_XML_NAME,
                member.is_dir())):
            continue

        with (src_cfile.open(member.name, 'r') as src_img,
              dst_cfile.open(member.name, 'w') as dst_img):

            try:
                _process_image(src_img, dst_img, **kwds)
            except UnidentifiedImageError as e:
                # skip non-images
                logger.warning(str(e))
                pass


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

    cfile = ComicArchive(out_path)

    add_paths = sorted(dst_path.iterdir())

    info_path = (src_path / COMICINFO_XML_NAME)
    if info_path.exists():
        add_paths = itertools.chain([info_path], add_paths)

    for path in add_paths:
        with open(path, 'rb') as f:
            cfile.add(path.name, f)

    logger.info(f'Created archive "{out_path}"')


_output_suffix = 'cbconvert'

def _output_filename(path, root=None):
    stem = '_'.join((path.stem, _output_suffix))
    return ((root or path.parent) / (str(stem) + '.cbz'))


def convert(files, root, **kwds):
    opts = {
        'size': (1860, 2480),
        'gamma' : 1/1.8,
        'format': 'JPEG',
        'quality': 85,
        'optimize': 1,
    }

    # TODO upscale images

    for cbx_path in expand_paths(files):

        out_path = _output_filename(cbx_path, root=root)

        if out_path.exists():
            logger.error(f'{out_path!s}: already exists')
            return 1

        src_cfile = ComicArchive(cbx_path)
        dst_cfile = ComicArchive(out_path)

        _convert_images(src_cfile, dst_cfile, **opts)
