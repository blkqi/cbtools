import logging
import pathlib
import time
import threading

from watchdog.events import FileSystemEventHandler

from cbtools.core import CBZFile, expand_paths
from cbtools.tag import AniList, cbtag

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

def worker():
    while True:
        path = manager_queue.dequeue()
        elapsed = 0

        if path:
            start = time.time()

            logger.debug(f'Processing {path}')
            cbtag([path], dryrun=True)

            end = time.time()
            elapsed = end - start

        if elapsed < 2:
            time.sleep(2 - elapsed)

manager_queue = ManagerQueue(300)
thread = threading.Thread(target=worker)
thread.daemon = True
thread.start()
