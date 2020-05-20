# Sophia
## Introduction
Sophia is a dictation tool used for all those note takers out there who's brains seem to always be one step ahead of their pen or pencil. Currently Sophia will dictate whatever you say, and will play and pause music on spotify if you give her access to your account. 
## Future Developemnt 
Sophia is a work in progress designed to be a platform to create a virtual assistant that can undertsand natural language and allow for easy expandability for anyone who would like to customize their own personal assistant.
In the future I would like to move away from the google dictation api and eliminate that dependancy and mvoe towards an open source option that can be processed without having to connect to a remote server, https://cmusphinx.github.io/wiki/ seems to be a good place for me to start, but this is simply a work in progress.
## How to run
```
git clone https://github.com/scottjoyner/Sophia.git
cd Sophia
pip install -r requirements.txt
cd sophia
python sophia.py
```

## Sophia_V0.2
Got rid of the Google API Dependancy and transfered over to a more complex model. The current Sophia_io.py file uses the DeepSpeech Mozilla Implementation. The speech accuracy is noticiably worse on this model, but does not require constant requests to the Google API. I would like to train my own model with common words within my vocabulary, as the open speech data used is great, but using thousands of hours of NPR doesn't train vulgaur language effectively at all.
## How to run New Version
You will need to download the traing models for this to run or you can provide your own models.
```
git clone https://github.com/scottjoyner/Sophia.git
cd Sophia
pip install -r requirements.txt
wget https://github.com/mozilla/DeepSpeech/releases/download/v0.7.0/deepspeech-0.7.0-models.pbmm
wget https://github.com/mozilla/DeepSpeech/releases/download/v0.7.0/deepspeech-0.7.0-models.scorer
python Sophia_io.py -m deepspeech-0.7.0-models.pbmm -s deepspeech-0.7.0-models.scorer
```
