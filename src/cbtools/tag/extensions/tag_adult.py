# This extension adds the tag 'Adult' to the comic if the 'isAdult' field is True.

def extension(cinfo, data):
    if data['isAdult']:
        if 'Tags' not in cinfo:
            cinfo['Tags'] = 'Adult'
        else:
            cinfo['Tags'] = ','.join([cinfo['Tags'], 'Adult'])
