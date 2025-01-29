from flask import Flask

from cbtools.config import config

app = Flask(__name__)

@app.route("/")
def info():
    return config

@app.route("/rescan", methods=['POST'])
def rescan():
    # TODO: force rescan the library
    return ""

@app.route("/flush", methods=['POST'])
def flush():
    # TODO: force flush the queue
    return ""
