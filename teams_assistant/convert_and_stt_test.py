import wave
import audioop
from openai import OpenAI
import yaml

src_path = "stereo_mix_test.wav"
dst_path = "stereo_mix_test_16k_mono.wav"

with wave.open(src_path, "rb") as wf:
    ch = wf.getnchannels()
    sw = wf.getsampwidth()
    sr = wf.getframerate()
    pcm = wf.readframes(wf.getnframes())

if ch == 2:
    pcm = audioop.tomono(pcm, sw, 0.5, 0.5)

state = None
pcm16k, state = audioop.ratecv(pcm, sw, 1, sr, 16000, state)

with wave.open(dst_path, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(sw)
    wf.setframerate(16000)
    wf.writeframes(pcm16k)

cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))
stt = cfg["stt"]
client = OpenAI(api_key=stt["api_key"], base_url=stt["base_url"])
with open(dst_path, "rb") as f:
    resp = client.audio.transcriptions.create(model=stt["model"], file=f)
text = resp if isinstance(resp, str) else getattr(resp, "text", None) or str(resp)
print(text)