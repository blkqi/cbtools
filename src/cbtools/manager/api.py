import pathlib

from flask import Flask, request

from cbtools.config import config
from cbtools.constants import SUPPORTED_FILE_EXTENSIONS as _extensions
from cbtools.manager.queue import manager_queue

app = Flask(__name__)

@app.route("/")
def info():
    return config

@app.route("/rescan", methods=['POST'])
def rescan():
    library = pathlib.Path(config['manager.library_path'])
    body = request.get_json(force=True, silent=True) or {}

    if body.get('paths'):
        for path in body['paths']:
            path = pathlib.Path(config['manager.library_path']) / path

            if path.exists():
                manager_queue.enqueue(path)
    else:
        for path in library.iterdir():
            if path.is_dir():
                if any(f.suffix.lower() in _extensions for f in path.iterdir()):
                    manager_queue.enqueue(path)

    return ("", 204)

@app.route("/flush", methods=['POST'])
def flush():
    manager_queue.flush()

    return ("", 204)

@app.route("/clear", methods=['POST'])
def clear():
    manager_queue.clear()

    return ("", 204)

@app.route("/list", methods=['GET'])
def list_items():
    items = manager_queue.list_items()

    return items
