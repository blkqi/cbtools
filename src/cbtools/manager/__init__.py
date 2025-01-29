import logging
import pathlib
import time
import threading

from watchdog.events import FileSystemEventHandler

from cbtools.core import CBZFile, expand_paths
from cbtools.tag import AniList

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.setLevel(logging.DEBUG)

class LibraryHandler(FileSystemEventHandler):
    def on_created(self, event):
        path = pathlib.Path(event.src_path)

        if path.suffix.lower() == '.cbz':
            logger.debug(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)
        elif path.name == '.anilist.txt':
            logger.debug(f'.anilist.txt update in {path.parent}')
            manager_queue.enqueue(path.parent)

class ManagerQueueItem:
    def __init__(self, item):
        self.item = item
        self.time = time.time()

class ManagerQueue:
    def __init__(self, delay):
        self.queue = []
        self.delay = delay

    def enqueue(self, item):
        if self._is_queued(item):
            return

        self.queue.append(ManagerQueueItem(item))

    def dequeue(self):
        if self._is_next_ready():
            return self.queue.pop(0).item

        return None

    def flush(self):
        for item in self.queue:
            item.time = 0

    def _is_queued(self, item):
        return item in [q.item for q in self.queue]

    def _is_next_ready(self):
        return not self._is_empty() and time.time() - self.queue[0].time >= self.delay

    def _is_empty(self):
        return len(self.queue) == 0

def get_series_id(folder):
    try:
        with open(folder / '.anilist.txt') as file:
            return int(file.read().strip())
    except FileNotFoundError:
        return None

def tag(folder, series_id, dryrun):
    cinfo = AniList().search(series_id).to_cinfo()

    for path in folder.iterdir():
        if path.suffix.lower() != '.cbz':
            continue

        with CBZFile(path) as cfile:
            if cfile.volume:
                cinfo['Volume'] = cfile.volume

            diff = cfile.info.compare(cinfo, excluding=['Notes'])

            if not diff:
                logger.info(f'{path}: no changes required')
                continue

            if dryrun:
                for item in diff:
                    print(item)
            else:
                cfile.update_cinfo(cinfo)

def worker():
    while True:
        path = manager_queue.dequeue()

        if path:
            logger.debug(f'Processing {path}')

            series_id = get_series_id(path)

            if not series_id:
                logger.warning(f'No series ID found for {path}')
                continue

            tag(path, series_id, True)
        else:
            time.sleep(10)

manager_queue = ManagerQueue(300)
thread = threading.Thread(target=worker)
thread.daemon = True
thread.start()
