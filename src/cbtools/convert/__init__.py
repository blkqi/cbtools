import kindlecomicconverter.comic2ebook
from types import SimpleNamespace

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def convert(path: Path, **kwds):
    opts = SimpleNamespace(**kwds)
    kindlecomicconverter.comic2ebook.checkOptions(opts)
    kindlecomicconverter.comic2ebook.makeBook(path)
