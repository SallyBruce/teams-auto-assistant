import wave
import pyaudio

DEVICE_INDEX = 11
RATE = 48000
CHANNELS = 1
SECONDS = 5
CHUNK = 1024
OUT = "stereo_mix_ch1.wav"

pa = pyaudio.PyAudio()
stream = pa.open(
    format=pyaudio.paInt16,
    channels=CHANNELS,
    rate=RATE,
    input=True,
    frames_per_buffer=CHUNK,
    input_device_index=DEVICE_INDEX,
)

frames = []
for _ in range(int(RATE / CHUNK * SECONDS)):
    frames.append(stream.read(CHUNK, exception_on_overflow=False))

stream.stop_stream()
stream.close()
pa.terminate()

wf = wave.open(OUT, "wb")
wf.setnchannels(CHANNELS)
wf.setsampwidth(2)
wf.setframerate(RATE)
wf.writeframes(b"".join(frames))
wf.close()

print("saved:", OUT)