from cbtools import configure_logging
from cbtools.config import config
from cbtools.functools import compose

from PIL import Image, ImageOps, UnidentifiedImageError


logger = configure_logging()

# disable decompression bomb detection
Image.MAX_IMAGE_PIXELS = None


def size(path):
    try:
        return Image.open(path).size
    except UnidentifiedImageError:
        return None


def grayscale(image):
    return image.convert('L')


def _is_spread(image):
    return image.size[0] > image.size[1]


def rotate_spreads(image):
    if _is_spread(image):
        return image.rotate(90, resample=Image.Resampling.BICUBIC, expand=True)
    return image


def _gamma_table(gamma, gain):
    return [min(255, int((x / 255.) ** (1. / gamma) * gain * 255.)) for x in range(256)]


def correct_gamma(image, gamma=config['image.gamma'], gain=config['image.gain']):
    assert(image.mode == 'L')
    return ImageOps.autocontrast(image.point(_gamma_table(gamma, gain)))


def _padding_method(image, size):
    if not any((p >= q) for (p, q) in zip(image.size, size)):
        return Image.Resampling.LANCZOS
    return Image.Resampling.BICUBIC


def resize(image, size=config['image.size'], color=config['image.background']):
    method = _padding_method(image, size)
    return ImageOps.pad(image, size, method=method, color=color)


def convert(path, root):
    _convert = compose(resize, correct_gamma, rotate_spreads, grayscale)
    _convert(Image.open(path)).save((root / path.name), config['image.format'],
                                    optimize=config['image.optimize'],
                                    quality=config['image.quality'])

def convert_to_webp(root):
    for img_path in root.rglob('*'):
        if not img_path.is_file():
            continue

        if img_path.suffix.lower() in ['.jpg', '.jpeg']:
            try:
                im = Image.open(img_path)
                webp_path = img_path.with_suffix('.webp')
                im.save(webp_path, 'WEBP', quality=75, method=6)
                img_path.unlink()
            except Exception as e:
                logger.error(f'Failed to convert {img_path} to webp: {e}')

