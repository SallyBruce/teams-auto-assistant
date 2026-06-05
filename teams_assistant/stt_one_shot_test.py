from openai import OpenAI
import yaml

cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))
stt = cfg["stt"]

client = OpenAI(api_key=stt["api_key"], base_url=stt["base_url"])
model = stt["model"]

wav_path = "stereo_mix_ch2.wav"  # 改成你 record_test.py 生成的文件名

with open(wav_path, "rb") as f:
    resp = client.audio.transcriptions.create(model=model, file=f)

text = resp if isinstance(resp, str) else getattr(resp, "text", None) or str(resp)
print(text)