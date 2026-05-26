"""Facing-page spread detection and joining.

Adapted from the stitch_spreads tool. Operates on a directory of extracted
(and typically upscaled) images: pairs adjacent pages in sorted filename
order, scores their inner edges via normalised cross-correlation of the
vertical brightness profile (gated by content density / contrast), and
when the score clears the threshold replaces the pair with a single
landscape image.

Designed for manga (right-to-left): given files (a, b) at indices (i, i+1),
``a`` is treated as the RIGHT page and ``b`` as the LEFT page when stitching.
"""

import numpy as np

from PIL import Image

from cbtools.config import config
from cbtools.constants import COMICINFO_XML_NAME
from cbtools.log import logger


_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}


def _ribbon_width(page_width):
    frac = config['stitch.ribbon_fraction']
    lo = config['stitch.ribbon_min_px']
    hi = config['stitch.ribbon_max_px']
    return max(lo, min(hi, int(round(page_width * frac))))


def _resample(profile, target_len):
    if profile.size == target_len:
        return profile
    src_x = np.linspace(0.0, 1.0, profile.size)
    dst_x = np.linspace(0.0, 1.0, target_len)
    return np.interp(dst_x, src_x, profile)


def _score_pair(left_img, right_img):
    """Score the gutter-edge correlation of a candidate spread.

    ``left_img`` will sit on the LEFT of the stitched output (its RIGHT
    edge is the gutter). ``right_img`` will sit on the RIGHT (its LEFT
    edge is the gutter). Returns a float in roughly [-1, 1]; zero is
    returned when a content/contrast gate rejects the pair.
    """
    white = config['stitch.white_pixel_value']
    min_density = config['stitch.min_content_density']
    min_contrast = config['stitch.min_edge_contrast']

    L = np.asarray(left_img.convert('L'))
    R = np.asarray(right_img.convert('L'))

    rw_l = _ribbon_width(L.shape[1])
    rw_r = _ribbon_width(R.shape[1])

    strip_l = L[:, L.shape[1] - rw_l:]   # right edge of left page
    strip_r = R[:, :rw_r]                # left edge of right page

    density_l = float(np.mean(strip_l < white))
    density_r = float(np.mean(strip_r < white))

    prof_l = strip_l.mean(axis=1).astype(np.float64)
    prof_r = strip_r.mean(axis=1).astype(np.float64)

    if prof_l.size != prof_r.size:
        target = max(prof_l.size, prof_r.size)
        prof_l = _resample(prof_l, target)
        prof_r = _resample(prof_r, target)

    std_l = float(prof_l.std())
    std_r = float(prof_r.std())

    if density_l < min_density or density_r < min_density:
        return 0.0
    if std_l < min_contrast or std_r < min_contrast:
        return 0.0

    zl = (prof_l - prof_l.mean()) / std_l
    zr = (prof_r - prof_r.mean()) / std_r
    return float(np.mean(zl * zr))


def _match_height(img, target_h):
    if img.height == target_h:
        return img
    new_w = max(1, int(round(img.width * target_h / img.height)))
    return img.resize((new_w, target_h), Image.Resampling.LANCZOS)


def _stitch(left_img, right_img):
    target_h = max(left_img.height, right_img.height)
    left_img = _match_height(left_img, target_h)
    right_img = _match_height(right_img, target_h)
    mode = left_img.mode if left_img.mode == right_img.mode else 'RGB'
    if left_img.mode != mode:
        left_img = left_img.convert(mode)
    if right_img.mode != mode:
        right_img = right_img.convert(mode)
    out = Image.new(mode, (left_img.width + right_img.width, target_h))
    out.paste(left_img, (0, 0))
    out.paste(right_img, (left_img.width, 0))
    return out


def join_spreads(src_path):
    """Detect facing-page spreads in ``src_path`` and merge them in place.

    Files are paired by sorted filename. Pages flagged as a spread are
    replaced with a single landscape image written under the first file's
    name; the second file of the pair is removed. Non-image files and a
    configurable number of leading pages (covers, ToC, ...) are skipped.
    """
    skip = config['stitch.skip_pages']
    threshold = config['stitch.correlation_threshold']

    paths = sorted(
        p for p in src_path.iterdir()
        if p.is_file()
        and p.suffix.lower() in _IMG_EXTS
        and p.name != COMICINFO_XML_NAME
    )

    if len(paths) < 2:
        return

    logger.info('Joining facing-page spreads')

    interior = paths[skip:]
    merged = 0

    i = 0
    while i + 1 < len(interior):
        a, b = interior[i], interior[i + 1]
        try:
            ima = Image.open(a)
            ima.load()
            imb = Image.open(b)
            imb.load()
        except Exception as e:
            logger.warning(f'stitch: failed to open {a.name} / {b.name}: {e}')
            i += 2
            continue

        # Manga RTL: file `a` (lower index) is the RIGHT page,
        # file `b` (higher index) is the LEFT page.
        score = _score_pair(imb, ima)

        if score >= threshold:
            spread = _stitch(imb, ima)
            # Write under `a`'s name so sort order is preserved for the
            # downstream stages; drop `b`.
            ima.close()
            imb.close()
            spread.save(a)
            b.unlink()
            logger.debug(f'stitch: merged {a.name} + {b.name} (score={score:.3f})')
            merged += 1
        else:
            ima.close()
            imb.close()
            logger.debug(f'stitch: keep split {a.name} + {b.name} (score={score:.3f})')

        i += 2

    logger.info(f'Joined {merged} spread(s)')
