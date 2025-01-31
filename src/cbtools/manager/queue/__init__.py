import time

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

manager_queue = ManagerQueue(300)
