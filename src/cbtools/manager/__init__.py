import asyncio
import logging
import requests
import time
import threading

from pathlib import Path
from waitress import serve
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler, FileSystemEvent

from cbtools.log import logger
from cbtools.config import config
from cbtools.core import ComicArchive, expand_paths
from cbtools.exceptions import CbtoolsError
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

processing_items = set()

class LibraryHandler(PatternMatchingEventHandler):
    def __init__(self):
        patterns = [f'*.{ext}' for ext in list(ComicArchive._allowed_file_exts.keys()) + ['txt']]

        super().__init__(patterns=patterns, ignore_patterns=[], ignore_directories=True, case_sensitive=False)

    def on_created(self, event):
        path = Path(event.src_path)

        if path.parent in processing_items:
            return

        self._handle_comic_archive(path)
        self._handle_series_id_file(path)

    def on_modified(self, event):
        path = Path(event.src_path)

        self._handle_series_id_file(path)

    def _handle_comic_archive(self, path):
        if path.suffix.strip('.').lower() in ComicArchive._allowed_file_exts.keys():
            logger.debug(f'CBZ file {path.name} updated in {path.parent}')
            manager_queue.enqueue(path.parent)

    def _handle_series_id_file(self, path):
        if path.name == config['tag.series_id_filename'] and path.parent not in processing_items:
            logger.debug(f"{config['tag.series_id_filename']} update in {path.parent}")
            manager_queue.enqueue(path.parent)


async def is_folder_write_inprogress(path):
    def get_folder_size(path):
        return sum(f.stat().st_size for f in path.glob('*') if f.is_file())

    total_size_before = get_folder_size(path)
    await asyncio.sleep(2)
    total_size_after = get_folder_size(path)

    return total_size_before != total_size_after


async def worker():
    while True:
        path = manager_queue.dequeue()

        if not path:
            await asyncio.sleep(10)
            continue

        if await is_folder_write_inprogress(path):
            logger.debug(f"Folder {path} is still being written, skipping...")
            manager_queue.enqueue(path)
            continue

        processing_items.add(path)

        logger.debug(f'Processing {path}')

        try:
            repack([path], remove_source=True, dryrun=config['manager.test_mode'])
        except CbtoolsError as e:
            logger.error(e)
            processing_items.remove(path)
            continue

        try:
            tag([path], dryrun=config['manager.test_mode'])
        except CbtoolsError as e:
            logger.error(e)
            processing_items.remove(path)
            continue

        try:
            rename([path], dryrun=config['manager.test_mode'], root=config['manager.library_path'])
        except CbtoolsError as e:
            logger.error(e)
            processing_items.remove(path)
            continue

        logger.debug(f'Finished processing {path}')

        processing_items.remove(path)



def rescan(files=None):
    body = {}

    if files:
        paths = expand_paths(files)
        directories = {str(path.parent.resolve()) for path in paths}

        if not directories:
            logger.warning('No valid directories found in paths')
            return

        body = {'paths': list(directories)}

    return requests.post(f"{API_BASE_URL}/rescan", json=body)


def flush():
    return requests.post(f"{API_BASE_URL}/flush")


def list_items():
    return requests.get(f"{API_BASE_URL}/list")


def clear():
    return requests.post(f"{API_BASE_URL}/clear")


def manager():
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
