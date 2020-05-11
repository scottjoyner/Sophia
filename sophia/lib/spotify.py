import requests
import json

class Spotify:
    def __init__():
        this.currentStatusInfo = upDateCurrentPlayBack()
        self.volume = currentStatusInfo['device']['volume_percent']
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': 'Bearer BQBDlEI_Vo7jS4iORezKaGnzYergl5FaISIHzJv_kY5iYlvp8A__0-eDvWDe8MNnUL_c76Vyrbcxx4OCRr-a_0v8VyQuWIf7bJEAA7bXmEKNmWL7G2_88h5Yqbd_afw7DIQgO5Wi3k2GeKDnv6Bmhrxfx4mxqQbNaWrYQUfQHMyfH4sbsxyV1Us_9dtZ2Avi9k7LHE0Pn7uvJTVsrWTkC34tcXTertEoGsj_TgJo8sssgdQpXnDpWKUYfO2BIJNnQnMpxgtTCc8IDPvN6w',
    }

    def play():
        response = requests.put('https://api.spotify.com/v1/me/player/play', headers=headers)

    def pause():
        response = requests.put('https://api.spotify.com/v1/me/player/pause', headers=headers)

    def next():
        response = requests.post('https://api.spotify.com/v1/me/player/next', headers=headers)

    def prevous():
        response = requests.post('https://api.spotify.com/v1/me/player/previous', headers=headers)

    def getCurrentArtist():
        response = requests.get('https://api.spotify.com/v1/me/player/currently-playing', headers=headers)
        current_song_dict = json.loads(response.text)
        return current_song_dict['item']['album']['artists'][0]['name']

    def getCurrentAlbum():
        response = requests.get('https://api.spotify.com/v1/me/player/currently-playing', headers=headers)
        current_song_dict = json.loads(response.text)
        return current_song_dict['item']['album']['name']

    def getCurrentSong():
        response = requests.get('https://api.spotify.com/v1/me/player/currently-playing', headers=headers)
        current_song_dict = json.loads(response.text)
        return current_song_dict['item']['name']

    def setVolume(volume):
        params = (
            ('volume_percent', volume),
        )
        response = requests.put('https://api.spotify.com/v1/me/player/volume', headers=headers, params=params)


    def getDevices():
        response = requests.get('https://api.spotify.com/v1/me/player/devices', headers=headers)
        return json.loads(response.txt)

    def upDateCurrentPlayBack():
        response = requests.get('https://api.spotify.com/v1/me/player', headers=headers)
        return json.loads(response.txt)

