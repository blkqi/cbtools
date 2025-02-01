import asyncio
import logging
import pathlib
import time
import threading

from logging import Formatter
from logging.handlers import TimedRotatingFileHandler
from typing import Set
from waitress import serve
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from cbtools.config import config, create_log_dir
from cbtools.core import CBZFile, expand_paths
from cbtools.manager.api import app
from cbtools.manager.queue import manager_queue
from cbtools.tag import AniList, cbtag
from cbtools.rename import cbrename

create_log_dir()
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.addHandler(TimedRotatingFileHandler(
    filename=config['log_path'] / 'cbmanager.log',
    when='D',
    interval=1,
    backupCount=6,
    encoding='utf-8',
    delay=False,
))
logger.setLevel(logging.DEBUG)

processing_items: Set[pathlib.Path] = set()

class LibraryHandler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        path = pathlib.Path(event.src_path)

        if path.parent in processing_items:
            logger.debug(f'Skipping {path.name} as the folder is currently processing')
            return

        if path.suffix.lower() == '.cbz':
            logger.debug(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)

    def on_modified(self, event: FileSystemEvent) -> None:
        path = pathlib.Path(event.src_path)

        if path.parent in processing_items:
            logger.debug(f'Skipping {path.name} as the folder is currently processing')
            return

        if path.name == config['seriesid_filename']:
            logger.debug(f"{config['seriesid_filename']} update in {path.parent}")
            manager_queue.enqueue(path.parent)

async def worker() -> None:
    while True:
        path = manager_queue.dequeue()
        elapsed = 0

        if path:
            start = time.time()
            processing_items.add(path)

            logger.debug(f'Processing {path}')

            # TODO: these i/o bound ops should run async
            cbtag([path], dryrun=config['test_mode'])
            cbrename([path], dryrun=config['test_mode'], root=config['library_path'])

            processing_items.remove(path)
            end = time.time()
            elapsed = end - start

        if elapsed < 2:
            await asyncio.sleep(2 - elapsed)

def cbmanager() -> None:
    handler = LibraryHandler()
    observer = Observer()
    observer.schedule(handler, path=config['library_path'], recursive=True)
    observer.start()
    thread = threading.Thread(target=serve, args=(app,), kwargs={'host': '0.0.0.0', 'port': 8080}, daemon=True)
    thread.start()

    try:
        asyncio.run(worker())
    except KeyboardInterrupt:
        logger.info('Shutting down...')

    observer.stop()
    observer.join()
