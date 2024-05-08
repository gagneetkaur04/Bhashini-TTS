import sys
import os
#replace the path with your hifigan path to import Generator from models.py 
sys.path.append("hifigan")
import argparse
import torch
from espnet2.bin.tts_inference import Text2Speech
from models import Generator
from scipy.io.wavfile import write
from meldataset import MAX_WAV_VALUE
from env import AttrDict
import json
import yaml
from text_preprocess_for_inference import TTSDurAlignPreprocessor, CharTextPreprocessor, TTSPreprocessor
import time
import pickle

SAMPLING_RATE = 22050

def load_hifigan_vocoder(language, gender, device):
    # Load HiFi-GAN vocoder configuration file and generator model for the specified language and gender
    vocoder_config = f"vocoder/{gender}/aryan/hifigan/config.json"
    vocoder_generator = f"vocoder/{gender}/aryan/hifigan/generator"
    # Read the contents of the vocoder configuration file
    with open(vocoder_config, 'r') as f:
        data = f.read()
    json_config = json.loads(data)
    h = AttrDict(json_config)
    torch.manual_seed(h.seed)
    # Move the generator model to the specified device (CPU or GPU)
    device = torch.device(device)
    generator = Generator(h).to(device)
    state_dict_g = torch.load(vocoder_generator, device)
    generator.load_state_dict(state_dict_g['generator'])
    generator.eval()
    generator.remove_weight_norm()

    # Return the loaded and prepared HiFi-GAN generator model
    return generator


def load_fastspeech2_model(language, gender, device):
    
    #updating the config.yaml fiel based on language and gender
    with open(f"{language}/{gender}/model/config.yaml", "r") as file:      
     config = yaml.safe_load(file)
    
    current_working_directory = os.getcwd()
    feat="model/feats_stats.npz"
    pitch="model/pitch_stats.npz"
    energy="model/energy_stats.npz"
    
    feat_path=os.path.join(current_working_directory,language,gender,feat)
    pitch_path=os.path.join(current_working_directory,language,gender,pitch)
    energy_path=os.path.join(current_working_directory,language,gender,energy)

    
    config["normalize_conf"]["stats_file"]  = feat_path
    config["pitch_normalize_conf"]["stats_file"]  = pitch_path
    config["energy_normalize_conf"]["stats_file"]  = energy_path
        
    with open(f"{language}/{gender}/model/config.yaml", "w") as file:
        yaml.dump(config, file)
    
    tts_model = f"{language}/{gender}/model/model.pth"
    tts_config = f"{language}/{gender}/model/config.yaml"
    
    
    return Text2Speech(train_config=tts_config, model_file=tts_model, device=device)

startTime_fast2speech = time.time()
model = load_fastspeech2_model('hindi', 'male', 'gpu')
endTime_fast2speech = time.time()
print("Loading Fast2Speech Model takes ", endTime_fast2speech-startTime_fast2speech, "seconds")

def text_synthesis(language, gender, sample_text, vocoder, MAX_WAV_VALUE, device, alpha):
    # Perform Text-to-Speech synthesis
    startTime_ts = time.time()

    with torch.no_grad():
        # Load the FastSpeech2 model for the specified language and gender

        # startTime_fast2speech = time.time()
        # Unpickle the model loader
        # if os.path.isfile("fastspeech_model.pkl"):
        #     with open("fastspeech_model.pkl", "rb") as f:
        #         model = pickle.load(f)

        #     # model = model(language, gender, device)
        # else:

        #     model = load_fastspeech2_model(language, gender, device)

        #     with open("fastspeech_model.pkl", "wb") as f:
        #         pickle.dump(model, f)

        # endTime_fast2speech = time.time()
        # print("Loading Fast2Speech Model takes ", endTime_fast2speech-startTime_fast2speech, "seconds")

        startTime_mel = time.time()
        # Generate mel-spectrograms from the input text using the FastSpeech2 model
        out = model(sample_text, decode_conf={"alpha": alpha})
        print("TTS Done")  

        endTime_mel = time.time()
        print("Generating mel-spectrograms from the input text using the FastSpeech2 model takes ", endTime_mel-startTime_mel, "seconds")

        startTime_audio = time.time()
        x = out["feat_gen_denorm"].T.unsqueeze(0) * 2.3262
        x = x.to(device)
        
        # Use the HiFi-GAN vocoder to convert mel-spectrograms to raw audio waveforms
        y_g_hat = vocoder(x)
        audio = y_g_hat.squeeze()
        audio = audio * MAX_WAV_VALUE
        audio = audio.cpu().numpy().astype('int16')

        endTime_audio = time.time()
        print("Audio Generation: ", endTime_audio-startTime_audio, "seconds")
        
        endTime_ts = time.time()
        print("TS takes ", endTime_ts-startTime_ts, "seconds")

        # Return the synthesized audio
        return audio
    
def split_into_chunks(text, words_per_chunk=100):
    words = text.split()
    chunks = [words[i:i + words_per_chunk] for i in range(0, len(words), words_per_chunk)]
    return [' '.join(chunk) for chunk in chunks]


if __name__ == "__main__":
    start_time_file = time.time()

    parser = argparse.ArgumentParser(description="Text-to-Speech Inference")
    parser.add_argument("--language", type=str, required=True, help="Language (e.g., hindi)")
    parser.add_argument("--gender", type=str, required=True, help="Gender (e.g., female)")
    parser.add_argument("--sample_text", type=str, required=True, help="Text to be synthesized")
    parser.add_argument("--output_file", type=str, default="", help="Output WAV file path")
    parser.add_argument("--alpha", type=float, default=1, help="Alpha Parameter")

    args = parser.parse_args()

    phone_dictionary = {}
    # Set the device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("Device: ",device)

    # Load the HiFi-GAN vocoder with dynamic language and gender
    startTime_loadHifi = time.time()
    vocoder = load_hifigan_vocoder(args.language, args.gender, device)
    endTime_loadHifi = time.time()
    print("Loading Hifigan Vocoder takes ", endTime_loadHifi-startTime_loadHifi, "seconds")

    
    if args.language == "urdu" or args.language == "punjabi":
            preprocessor = CharTextPreprocessor()
    elif args.language == "english":
            preprocessor = TTSPreprocessor()
    else:
            preprocessor = TTSDurAlignPreprocessor()


    import concurrent.futures
    import numpy as np
    import time

    startTime_A1 = time.time()

    start_time = time.time()
    audio_arr = []  # Initialize an empty list to store audio samples
    result = split_into_chunks(args.sample_text)

    # Use ThreadPoolExecutor for concurrent execution
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Process each text sample concurrently
        for sample_text in result:
            #print("sample_text -- ", sample_text)
            
            # Preprocess the text and obtain a list of phrases
            preprocessed_text, phrases = preprocessor.preprocess(sample_text, args.language, args.gender, phone_dictionary)
            preprocessed_text = " ".join(preprocessed_text)

            startTime_A2 = time.time()

            # Generate audio from the preprocessed text using a text-to-speech synthesis function
            audio = text_synthesis(args.language, args.gender, preprocessed_text, vocoder, MAX_WAV_VALUE, device, args.alpha)
            
            endTime_A2 = time.time()
            print("A2 takes ", endTime_A2-startTime_A2, "seconds")
            
            # Set the output file name
            if args.output_file:
                output_file = f"{args.output_file}"
            else:
                output_file = f"{args.language}_{args.gender}_output.wav"

            # Append the generated audio to the list
            audio_arr.append(audio)
    result_array = np.concatenate(audio_arr, axis=0)
    write(output_file, SAMPLING_RATE, result_array)

    endTime_A1 = time.time()
    print("A1 takes ", endTime_A1-startTime_A1, "seconds")

    end_time_file = time.time()
    total_time = end_time_file - start_time_file
    print('Total Time Taken: ', total_time)