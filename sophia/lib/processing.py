import nltk
from lib.spotify import Spotify


class Processing:
#    def __init__(self, input):
#        self.input = input
#        self.tokens = word_tokenize(input)
    music_list = ['spotify', 'play', 'pause', 'next', 'previous', 'last', 'song', 'shuffle', 'volume', 'up', 'down', 'artist', 'album', 'speaker']

    def processInput(input):
        if 'sophia' in input:
            if 'play' in input: # Think and not saying an noun
                spotify.play()
            elif 'pause' in input:
                spotify.pause()
            elif 'next' in input:
                spotify.next()
            elif 'prevous' in input or 'back':
                spotify.next()
            elif 'who' in input and 'sings':
                print(spotify.getCurrentArtist())
            elif 'what' in input and 'song':
                print(spotify.getCurrentSong)
            elif 'volume' in input:
                if 'percent' in input:
                    for x in self.tokens:
                        if x.isdigit():
                            spotify.setVolume(x)
                elif 'down' in input:
                    spotify.lowerVolume()
                elif 'up' in input:
                    spotify.increaseVolume()
            elif "calculate" in input.lower():
                # write your wolframalpha app_id here
                app_id = "WOLFRAMALPHA_APP_ID"
                client = wolframalpha.Client(app_id)

                indx = input.lower().split().index('calculate')
                query = input.split()[indx + 1:]
                res = client.query(' '.join(query))
                answer = next(res.results).text
                assistant_speaks("The answer is " + answer)
                return
