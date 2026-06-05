import pyaudio

pa = pyaudio.PyAudio()
print("Device count:", pa.get_device_count())
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    print(
        f"[{i}] {info.get('name')} | in={info.get('maxInputChannels')} | out={info.get('maxOutputChannels')} | rate={info.get('defaultSampleRate')} | hostApi={info.get('hostApi')}"
    )
pa.terminate()