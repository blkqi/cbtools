import time

class ManagerQueueItem:
    def __init__(self, item):
        self.item = item
        self.time = time.time()

class ManagerQueue:
    def __init__(self, delay):
        self._queue = []
        self._delay = delay

    def enqueue(self, item):
        if self._is_queued(item):
            return

        self._queue.append(ManagerQueueItem(item))

    def dequeue(self):
        if self._is_next_ready():
            return self._queue.pop(0).item

        return None

    def list_items(self):
        return [str(q.item) for q in self._queue]

    def clear(self):
        self._queue = []

    def flush(self):
        for item in self._queue:
            item.time = 0

    def _is_queued(self, item):
        return item in [q.item for q in self._queue]

    def _is_next_ready(self):
        return not self._is_empty() and time.time() - self._queue[0].time >= self._delay

    def _is_empty(self):
        return len(self._queue) == 0

manager_queue = ManagerQueue(20)
