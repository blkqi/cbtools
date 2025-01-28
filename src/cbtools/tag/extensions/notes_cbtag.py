# This extension sets the 'Notes' field with tagger information.

from datetime import datetime, timezone

def extension(cinfo, data):
    cinfo['Notes'] = f'Tagged by cbtools on {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}Z'
