import pathlib
import time
import threading

from watchdog.events import FileSystemEventHandler

class LibraryHandler(FileSystemEventHandler):
    def on_modified(self, event):
        path = pathlib.Path(event.src_path)

        if path.suffix.lower() == '.cbz':
            print(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)
        elif path.name == '.anilist.txt':
            print(f'.anilist.txt update in {path.parent}')
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
        if item in [q.item for q in self.queue]:
            return

        delay_item = ManagerQueueItem(item)
        self.queue.append(delay_item)

    def dequeue(self):
        if self._is_next_ready():
            return self.queue.pop(0).item

        return None

    def flush(self):
        for item in self.queue:
            item.time = 0

    def _is_next_ready(self):
        return not self._is_empty() and time.time() - self.queue[0].time >= self.delay

    def _is_empty(self):
        return len(self.queue) == 0

def worker():
    while True:
        path = manager_queue.dequeue()

        if path:
            print(f'Processing {path}')
        else:
            time.sleep(10)

manager_queue = ManagerQueue(300)
thread = threading.Thread(target=worker)
thread.daemon = True
thread.start()
