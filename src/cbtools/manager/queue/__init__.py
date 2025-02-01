import time

from typing import Any, List, Optional

class ManagerQueueItem:
    def __init__(self, item: Any) -> None:
        self.item = item
        self.time = time.time()

class ManagerQueue:
    def __init__(self, delay: float) -> None:
        self._queue: List[ManagerQueueItem] = []
        self._delay = delay

    def enqueue(self, item: Any) -> None:
        if self._is_queued(item):
            return

        self._queue.append(ManagerQueueItem(item))

    def dequeue(self) -> Optional[Any]:
        if self._is_next_ready():
            return self._queue.pop(0).item

        return None

    def list_items(self) -> List[str]:
        return [str(q.item) for q in self._queue]

    def clear(self) -> None:
        self._queue = []

    def flush(self) -> None:
        for item in self._queue:
            item.time = 0

    def _is_queued(self, item: Any) -> bool:
        return item in [q.item for q in self._queue]

    def _is_next_ready(self) -> bool:
        return not self._is_empty() and time.time() - self._queue[0].time >= self._delay

    def _is_empty(self) -> bool:
        return len(self._queue) == 0

manager_queue = ManagerQueue(300)
