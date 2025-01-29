# This extension sets the 'Count' field to the number of volumes if the series is finished.

def extension(cinfo, data):
    if data['status'].upper() == 'FINISHED' and data['volumes']:
        cinfo['Count'] = str(data['volumes'])
