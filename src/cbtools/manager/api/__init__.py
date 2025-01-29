import pathlib

from flask import Flask

from cbtools.config import config
from cbtools.manager import manager_queue

app = Flask(__name__)

@app.route("/")
def info():
    return config

@app.route("/rescan", methods=['POST'])
def rescan():
    library = pathlib.Path(config['library_path'])

    for path in library.iterdir():
        if path.is_dir():
            if any([f.suffix.lower() == '.cbz' for f in path.iterdir()]):
                manager_queue.enqueue(path)

    return ("", 204)

@app.route("/flush", methods=['POST'])
def flush():
    manager_queue.flush()

    return ("", 204)
