from pathlib import Path

from cbtools.manager.runtime import ManagerRuntime


class ManagerQueue(ManagerRuntime):
    def dequeue(self):
        job = self.claim()
        return Path(job["path"]) if job else None


manager_queue = ManagerQueue(20)
