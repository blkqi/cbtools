import json
import os

DEFAULT_CONFIG = {
    "library_path": "/library"
}

CONFIG_FILE_PATH = os.getenv('CONFIG_FILE_PATH', os.getcwd() + '/config.json')

def load_config():
    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONFIG

config = load_config()
