from flask import Flask

from cbtools.config import CONFIG

app = Flask(__name__)

@app.route("/")
def info():
    return CONFIG

@app.rescan("/rescan", methods=['POST'])
def config():
    return ""
