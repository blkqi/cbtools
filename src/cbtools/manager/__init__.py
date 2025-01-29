import time

from watchdog.events import FileSystemEventHandler

class LibraryHandler(FileSystemEventHandler):
    def on_modified(self, event):
        print(f'File modified: {event.src_path}')
