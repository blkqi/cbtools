# This extension adds the tag 'Highly Rated' to the comic if the average score is 85 or higher.

def extension(cinfo, data):
    if (data.get('averageScore') or 0) >= 85:
        if 'Tags' in cinfo and cinfo['Tags']:
            cinfo['Tags'] = ','.join([cinfo['Tags'], 'Highly Rated'])
        else:
            cinfo['Tags'] = 'Highly Rated'
