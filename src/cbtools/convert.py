import logging
import typing
import copy
import kindlecomicconverter.comic2ebook as kcc

from types import SimpleNamespace

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def convert(path, **kwds):
    kcc.options = SimpleNamespace(
        input = [],
        format = 'CBZ',
        title = 'defaulttitle',
        author = 'defaultauthor',
        delete = False,
        noprocessing = False,
        white_borders = False,
        black_borders = False,
        maximizestrips = False,
        hq = False,
        interpanelcrop = False,
        stretch = False,
        webtoon = False,
        forcepng = False,
        forcecolor = False,
        mozjpeg = False,
        upscale = True, # Resize images to device's resolution
        gamma = 0.0, # Automatic gamma correction
        splitter = 2, # Rotate spreads
        cropping = 0, # Disable cropping
        batchsplit = 0,
        customwidth = 0,
        customheight = 0,
        righttoleft = False,
        norotate = False,
        **kwds,
    )
    kcc.checkOptions(kcc.options)
    kcc.makeBook(str(path))
