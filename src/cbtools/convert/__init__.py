import logging
import typing
import copy
import kindlecomicconverter.comic2ebook as kcc

from types import SimpleNamespace

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def convert(path, **kwds):
    opts = SimpleNamespace(
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
        gamma = 0.0,
        cropping = 0,
        batchsplit = 0,
        customwidth = 0,
        customheight = 0,
    )
    kcc.options = copy.replace(opts, **kwds)
    kcc.checkOptions(kcc.options)
    kcc.makeBook(str(path))
