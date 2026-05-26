"""Facing-page spread detection and joining.

Pairs adjacent pages in sorted filename order, scores them via a
MobileNetV3-small binary classifier on a strip across the inner edge, and
when the score clears the threshold replaces the pair with a single
landscape image.

Detection is delegated to ``spreadnn`` for model loading, scoring, and
auto-alignment.  This module only handles the Pillow-based stitching.

Requires PyTorch and TorchVision (install via ``pip install cbtools[spreads]``).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from cbtools.config import config
from cbtools.log import logger


# --------------------------------------------------------------------------- #
# Stitching helpers
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def join_spreads(src_path, threshold=None, model_name=None):
    """Detect facing-page spreads in ``src_path`` and merge them in place.

    Alignment is auto-detected: starts at offset 1 (first page skipped),
    falls back to offset 0 if no spreads are found.

    Parameters
    ----------
    src_path : Path
        Directory of extracted images.
    threshold : float, optional
        Detection threshold.  Defaults to
        ``config['stitch.spread_probability']``.
    model_name : str, optional
        Model name or ``.pth`` path.  Defaults to bundled model.
    """
    from spreadnn.detect import detect as _detect

    threshold = threshold if threshold is not None else config['stitch.spread_probability']
    model_path = _resolve_model(model_name)

    results = _detect(src_path, threshold=threshold, model_path=model_path)
    merged = [r for r in results if r.merged]

    if not merged:
        logger.info('No spreads detected')
        return

    logger.info(f'Joining {len(merged)} facing-page spread(s)')

    for r in merged:
        a = Path(src_path) / r.even
        b = Path(src_path) / r.odd
        with Image.open(a) as im_a, Image.open(b) as im_b:
            spread = _stitch(im_b, im_a)
        spread.save(a)
        b.unlink()
        logger.info(f'[merge] {a.name} | {b.name}')

    logger.info(f'Joined {len(merged)} spread(s)')


def _resolve_model(model_name):
    if model_name is None:
        return None
    if model_name.endswith('.pth'):
        return model_name
    return None  # spreadnn will resolve bundled model by name
