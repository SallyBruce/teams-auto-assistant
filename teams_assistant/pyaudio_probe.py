import pyaudio

pa = pyaudio.PyAudio()

idx = 14
info = pa.get_device_info_by_index(idx)
print("Device info:", info)

fmt = pyaudio.paInt16
for ch in (1, 2, 4):
    for rate in (8000, 16000, 44100, 48000):
        ok = pa.is_format_supported(
            rate,
            input_device=idx,
            input_channels=ch,
            input_format=fmt,
        )
        if ok:
            print("SUPPORTED:", "channels=", ch, "rate=", rate)

pa.terminate()