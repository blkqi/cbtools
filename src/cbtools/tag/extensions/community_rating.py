# This extension sets the 'CommunityRating' field to the AniList percentage score converted to a 0-5 scale.

def extension(cinfo, data):
    if data['averageScore']:
        cinfo['CommunityRating'] = str(round(data['averageScore'] / 20, 1))
