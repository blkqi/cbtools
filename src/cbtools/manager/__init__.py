import asyncio
import logging
import requests
import time
import threading

from pathlib import Path
from typing import Set, List
from waitress import serve
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler, FileSystemEvent

from cbtools.log import logger
from cbtools.config import config
from cbtools.core import ComicArchive, expand_paths
from cbtools.manager.api import app
from cbtools.manager.queue import manager_queue
from cbtools.tag import AniList, tag
from cbtools.rename import rename
from cbtools.repack import repack
from cbtools.manager.api import app
from cbtools.manager.queue import manager_queue


API_BASE_URL = f"http://localhost:{config['manager.api_port']}"

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

processing_items: Set[Path] = set()

class LibraryHandler(PatternMatchingEventHandler):
    def __init__(self):
        patterns = [f'*.{ext}' for ext in list(ComicArchive._allowed_file_exts.keys()) + ['txt']]

        super().__init__(patterns=patterns, ignore_patterns=[], ignore_directories=True, case_sensitive=False)

    def on_created(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)

        if path.parent in processing_items:
            return

        self._handle_comic_archive(path)
        self._handle_series_id_file(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)

        self._handle_series_id_file(path)

    def _handle_comic_archive(self, path: Path) -> None:
        if path.suffix.strip('.').lower() in ComicArchive._allowed_file_exts.keys():
            logger.debug(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)

    def _handle_series_id_file(self, path: Path) -> None:
        if path.name == config['tag.series_id_filename'] and path.parent not in processing_items:
            logger.debug(f"{config['tag.series_id_filename']} update in {path.parent}")
            manager_queue.enqueue(path.parent)


async def worker() -> None:
    while True:
        path = manager_queue.dequeue()
        elapsed = 0

        if path:
            start = time.time()
            processing_items.add(path)

            logger.debug(f'Processing {path}')

            # TODO: i/o bound ops should run async
            # TODO: handle more errors

            try:
                repack([path], remove_source=True, dryrun=config['manager.test_mode'])
            except NameError as e:
                logger.error(e)
                processing_items.remove(path)
                continue

            try:
                tag([path], dryrun=config['manager.test_mode'])
            except NameError as e:
                logger.error(e)
                processing_items.remove(path)
                continue

            try:
                rename([path], dryrun=config['manager.test_mode'], root=config['manager.library_path'])
            except OSError as e:
                logger.error(e)
                processing_items.remove(path)
                continue

            logger.debug(f'Finished processing {path}')

            processing_items.remove(path)
            end = time.time()
            elapsed = end - start

        if elapsed < config['manager.processing_interval']:
            await asyncio.sleep(config['manager.processing_interval'] - elapsed)


def rescan(files: List[Path] = None) -> requests.Response:
    body = {}

    if files:
        paths = expand_paths(files)
        directories = {str(path.parent.resolve()) for path in paths}

        if not directories:
            logger.warning('No valid directories found in paths')
            return

        body = {'paths': list(directories)}

    return requests.post(f"{API_BASE_URL}/rescan", json=body)


def flush() -> requests.Response:
    return requests.post(f"{API_BASE_URL}/flush")


def list_items() -> requests.Response:
    return requests.get(f"{API_BASE_URL}/list")


def clear() -> requests.Response:
    return requests.post(f"{API_BASE_URL}/clear")


def manager() -> None:
    logger.info('Starting cbmanager...')

    handler = LibraryHandler()
    observer = Observer()
    observer.schedule(handler, path=config['manager.library_path'], recursive=True)
    observer.start()
    thread = threading.Thread(target=serve, args=(app,), kwargs={'host': '0.0.0.0', 'port': config['manager.api_port']}, daemon=True)
    thread.start()

    try:
        asyncio.run(worker())
    except KeyboardInterrupt:
        logger.info('Shutting down...')

    observer.stop()
    observer.join()
