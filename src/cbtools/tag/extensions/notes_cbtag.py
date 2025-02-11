from datetime import datetime, timezone

# This extension sets the 'Notes' field with tagger information.

def extension(cinfo, data):
    cinfo['Notes'] = f'Tagged by cbtools on {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}Z'
