import speech_recognition as sr
import threading, queue
import logging
import lib.processing as processing

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
            p = processing(text)
            p.processesInput()

        except sr.UnknownValueError:
            logger.info("Unkown Value Error")
        except sr.RequestError as e:
            logger.info("Request Error")
        q.task_done()
        # logger.info('%i items in queue', q.qsize())



# Location of the main method
if __name__ == "__main__":

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
                q.put(r.record(source, duration=10))
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




