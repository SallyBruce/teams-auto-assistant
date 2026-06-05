import pyaudio

pa = pyaudio.PyAudio()

stereo_mix = []
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    name = str(info.get("name", ""))
    if "Stereo Mix" in name:
        stereo_mix.append((i, info))

print("Stereo Mix candidates:")
for i, info in stereo_mix:
    print(i, info.get("name"), "in", info.get("maxInputChannels"), "out", info.get("maxOutputChannels"), "rate", info.get("defaultSampleRate"), "hostApi", info.get("hostApi"))

print("\nOpen tests:")
for idx, info in stereo_mix:
    name = info.get("name")
    for ch in (1, 2):
        for rate in (44100, 48000):
            try:
                s = pa.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=rate,
                    input=True,
                    frames_per_buffer=1024,
                    input_device_index=idx,
                )
                s.close()
                print("OK ", idx, name, "ch", ch, "rate", rate)
            except Exception as e:
                print("FAIL", idx, name, "ch", ch, "rate", rate, repr(e))

pa.terminate()