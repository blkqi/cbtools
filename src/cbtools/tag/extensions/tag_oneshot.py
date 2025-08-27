# This extension adds the tag 'Oneshot' to the comic if the comic has only one volume and is finished.

def extension(cinfo, data):
    if data['volumes'] == 1 and data['status'] == 'FINISHED':
        if 'Tags' in cinfo and cinfo['Tags']:
            cinfo['Tags'] = ','.join([cinfo['Tags'], 'Oneshot'])
        else:
            cinfo['Tags'] = 'Oneshot'
