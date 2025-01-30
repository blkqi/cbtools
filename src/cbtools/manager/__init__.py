import logging
import pathlib
import time
import threading

from watchdog.events import FileSystemEventHandler

from cbtools.config import config
from cbtools.core import CBZFile, expand_paths
from cbtools.tag import AniList, cbtag
from cbtools.rename import cbrename

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.setLevel(logging.DEBUG if config['test_mode'] else logging.INFO)

class LibraryHandler(FileSystemEventHandler):
    # TODO: we might need to ignore events originating from cbmanager

    def on_created(self, event):
        path = pathlib.Path(event.src_path)

        if path.parent in processing_items:
            logger.debug(f'Skipping {path.name} as the folder is currently processing')
            return

        if path.suffix.lower() == '.cbz':
            logger.debug(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)

    def on_modified(self, event):
        path = pathlib.Path(event.src_path)

        if path.parent in processing_items:
            logger.debug(f'Skipping {path.name} as the folder is currently processing')
            return

        if path.name == config['seriesid_filename']:
            logger.debug(f"{config['seriesid_filename']} update in {path.parent}")
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
            processing_items.add(path)

            logger.debug(f'Processing {path}')
            cbtag([path], dryrun=config['test_mode'])
            cbrename([path], dryrun=config['test_mode'], path=config['library_path'])

            processing_items.remove(path)
            end = time.time()
            elapsed = end - start

        if elapsed < 2:
            time.sleep(2 - elapsed)

processing_items = set()
manager_queue = ManagerQueue(300)
thread = threading.Thread(target=worker)
thread.daemon = True
thread.start()
