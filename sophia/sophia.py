import speech_recognition as sr
import threading, queue
import logging
import requests
import json

class Spotify:
    def __init__():
        this.currentStatusInfo = upDateCurrentPlayBack()
        self.volume = currentStatusInfo['device']['volume_percent']

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

    def playOnDevice(id):
        if id == "Frank":
            data = '{device_ids": ["cf03af39ab8f6ae0a09dd3fefadf80e9128aad81"]}'
        elif "DESKTOP" in id:
            data = '{device_ids": ["cd2a261ee7791075d5a7466c5a41eee03aa43f0b"]}'
        elif "google" in id or "speaker" in id:
            data = '{"device_ids":["f8811fc74c9e6891629cf8a7e8b4c5c2"]}'
    

        response = requests.put('https://api.spotify.com/v1/me/player', headers=headers, data=data)

    def getDevices():
        response = requests.get('https://api.spotify.com/v1/me/player/devices', headers=headers)
        return json.loads(response.txt)

    def upDateCurrentPlayBack():
        response = requests.get('https://api.spotify.com/v1/me/player', headers=headers)
        return json.loads(response.txt)



# Pipeline for communication between the threads
# Currently not in use, would like the ability for threads to communicate and coordinate what they are doing more in the future.
class Pipeline:
    """
    Class to allow a single element pipeline between producer and consumer.
    """
    def __init__(self):
        self.message = 0
        self.producer_lock = threading.Lock()
        self.consumer_lock = threading.Lock()
        self.consumer_lock.acquire()

    def get_message(self, name):
        logging.debug("%s:about to acquire getlock", name)
        self.consumer_lock.acquire()
        logging.debug("%s:have getlock", name)
        message = self.message
        logging.debug("%s:about to release setlock", name)
        self.producer_lock.release()
        logging.debug("%s:setlock released", name)
        return message

    def set_message(self, message, name):
        logging.debug("%s:about to acquire setlock", name)
        self.producer_lock.acquire()
        logging.debug("%s:have setlock", name)
        self.message = message
        logging.debug("%s:about to release getlock", name)
        self.consumer_lock.release()
        logging.debug("%s:getlock released", name)

def processInput(input):
    
    if 'Sophia' in input:
        if 'play' in input: # Think and not saying an noun
            Spotify.play()
        if 'pause' in input:
            Spotify.pause()
        if 'next' in input:
            Spotify.next()
        if 'prevous' in input or 'back':
            Spotify.next()
        if 'who' in input and 'sings':
            print(Spotify.getCurrentArtist())
        if 'what' in input and 'song':
            print(Spotify.getCurrentSong)
        if 'volume' in input:
            if 'percent' in input:
                for x in self.tokens:
                    if x.isdigit():
                        Spotify.setVolume(x)
            elif 'down' in input:
                Spotify.lowerVolume()
            elif 'up' in input:
                Spotify.increaseVolume()
        if "calculate" in input.lower():
            # write your wolframalpha app_id here
            app_id = "WOLFRAMALPHA_APP_ID"
            client = wolframalpha.Client(app_id)

            index = input.lower().split().index('calculate')
            query = input.split()[indx + 1:]
            res = client.query(' '.join(query))
            answer = next(res.results).text
            assistant_speaks("The answer is " + answer)
            


# Worker thread which processes the audio using Google API
# TODO Find an open source alternative for google.
def worker():
    while True:
        audio = q.get()
        if audio is None: break  # stop processing if the main thread is done
        try:
            text = r.recognize_google(audio, language ='en-US')
            # Loggs the text dictated to the source log file document.
            logger.error('%s', text)
            print(text)
            processInput(text)

        except sr.UnknownValueError:
            logger.info("Unkown Value Error")
        except sr.RequestError as e:
            logger.info("Request Error")
        q.task_done()
        # logger.info('%i items in queue', q.qsize())



# Location of the main method
if __name__ == "__main__":
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': 'Bearer BQBSQtlaLoSBn8WnYF7GSTGYt3BXe6VQPpFHN9TngEx80IMWYlbECX0vGzIdVU11I4XyX80gRhXOTbwjfXou4AdU_IIcGLaZrwPa3b8rCIxKxk3JUIBmlKcCsxVB2-SdMGhTFEOvui6IzQX4A2FRmnSoyIkw56ojfXmgC9lzAZ3nDnNVIVmec0FLsp3QqaZx1Tuor46ef84hak9bBiuaoCX4FjUzVoPqDtdezuSQpGE1mY-wBQqZrH9S2f2k41MkmH5g8Rdhg15aRtUZ4g',
    }
    # Create a custom logger
    logger = logging.getLogger(__name__)
    stenographer = logging.FileHandler('script.log')
    stenographer.setLevel(logging.ERROR)
    stenographer_format = logging.Formatter('%(asctime)s :  %(message)s')
    stenographer.setFormatter(stenographer_format)
    logger.addHandler(stenographer)

    # Custom Logger for streaming data and debugging
    console = logging.StreamHandler()
    console_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console.setFormatter(console_format)
    #console.setLevel(logging.ERROR)
    logger.addHandler(console)


    # Initalize the Queue
    q = queue.Queue()
    # initalize the Recognizer
    r = sr.Recognizer()
    # Start up the worker thread
    threading.Thread(target=worker, daemon=True).start()

    with sr.Microphone() as source:
        try:
            while True:  # repeatedly listen for phrases and put the resulting audio on the audio processing job queue
                q.put(r.record(source, duration=5))
        except KeyboardInterrupt:  # allow Ctrl + C to shut down the program
            pass
        logger.error("This shit is over")

# Joins the threads when the programs execution has finanlly been complteted.
q.join()

    # Alternative that looks easier to read and understand would be next 3 lines
#    recognize_thread = Thread(target=recognize_worker)
#    recognize_thread.daemon = True
#    recognize_thread.start()


#    rObject = sr.Recognizer()
#    m = sr.Microphone()
#    with m as source:
#        rObject.adjust_for_ambient_noise(source)  # we only need to calibrate once, before we start listening
#        stop_listening = rObject.listen_in_background(m, callback)
#
#    q.put(audio)
######################################
# Alternative to this would be a simplified version that can be seen down below
#    while(1):
#        rObject = sr.Recognizer()
#        audio = ''
#        with sr.Microphone() as source:
#            rObject.adjust_for_ambient_noise(source)
#            logger.error("listening")
#            # recording the audio using speech recognition
#            audio = rObject.listen(source)
#            logger.error("Audio Generated")
#        q.put(audio)

# block until all tasks are done




