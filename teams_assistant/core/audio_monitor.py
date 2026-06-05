import io
import audioop
import math
import time
import wave
from array import array
from dataclasses import dataclass
from queue import Queue
from threading import Event
from typing import Optional


@dataclass
class AudioConfig:
    # "microphone" 或 "system_loopback"
    # 注意：本项目不做任何双路混音逻辑，永远只打开单一路音源。
    mode: str
    device_index: int
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_ms: int
    min_segment_sec: float
    max_segment_sec: float
    vad_silence_ms: int
    vad_rms_threshold: float


def list_audio_devices() -> list[dict]:
    """返回可用输入设备列表（用于 --list-devices）。"""
    try:
        import pyaudio  # type: ignore
    except Exception as e:
        return [{"index": -1, "name": f"PyAudio not available: {e!r}", "maxInputChannels": 0}]

    pa = pyaudio.PyAudio()
    try:
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            devices.append(
                {
                    "index": i,
                    "name": info.get("name"),
                    "maxInputChannels": info.get("maxInputChannels"),
                    "defaultSampleRate": info.get("defaultSampleRate"),
                    "hostApi": info.get("hostApi"),
                }
            )
        return devices
    finally:
        pa.terminate()


def _rms_int16(pcm_bytes: bytes) -> float:
    if not pcm_bytes:
        return 0.0
    # int16 PCM -> array('h')
    arr = array("h")
    arr.frombytes(pcm_bytes)
    if len(arr) == 0:
        return 0.0
    # RMS (避免依赖 numpy，便于在缺少依赖时也能运行基础自测)
    s = 0.0
    for v in arr:
        s += float(v) * float(v)
    mean = s / float(len(arr))
    return float(math.sqrt(mean))


def _frames_to_wav_bytes(
    frames: list[bytes], sample_rate: int, channels: int, sample_width_bytes: int
) -> bytes:
    buff = io.BytesIO()
    with wave.open(buff, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width_bytes)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    return buff.getvalue()


class AudioMonitorThread:
    """
    录音线程：
    - 以较短 frame 读取音频（降低端到端延迟）
    - 通过简易静音检测将音频切成 1~5 秒片段，推入 STT Queue
    """

    def __init__(
        self,
        cfg: AudioConfig,
        stt_queue: Queue,
        stop_event: Event,
        gui_queue: Optional[Queue] = None,
    ) -> None:
        self.cfg = cfg
        self.stt_queue = stt_queue
        self.stop_event = stop_event
        self.gui_queue = gui_queue
        try:
            import pyaudio  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "PyAudio 未安装或不可用。请先安装 PyAudio（Windows 建议使用对应 Python 版本的 wheel）。"
            ) from e

        self._pyaudio = pyaudio
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._input_channels: Optional[int] = None

        # STT 侧统一优化为：16kHz + mono（大幅降低数据量与延迟）
        # 注意：输入设备不一定支持 16kHz/mono，所以我们：
        # 1) 优先尝试用 channels=1 打开流
        # 2) 若失败回退 channels=2
        # 3) 若回退为 2，发送前在内存中 tomono + resample 到 16kHz
        self._target_sample_rate = 16000
        self._ratecv_state = None  # 用于 audioop.ratecv 的连续状态

    def _open_stream(self):
        frames_per_buffer = int(self.cfg.sample_rate * self.cfg.frame_ms / 1000)

        def _try_open(channels: int):
            kwargs = dict(
                format=self._pyaudio.paInt16,
                channels=channels,
                rate=self.cfg.sample_rate,
                input=True,
                frames_per_buffer=frames_per_buffer,
                input_device_index=self.cfg.device_index,
            )

            if self.cfg.mode == "microphone":
                return self._pa.open(**kwargs)

            if self.cfg.mode == "system_loopback":
                try:
                    return self._pa.open(**kwargs, as_loopback=True)
                except TypeError:
                    raise RuntimeError(
                        "This PyAudio build does not support WASAPI loopback (missing as_loopback). "
                        "Please install a WASAPI-capable PyAudio build or switch to Microphone."
                    )

            raise ValueError(f"Unknown audio mode: {self.cfg.mode!r}")

        preferred = int(self.cfg.channels or 0)
        candidates = []
        if preferred > 0:
            candidates.append(preferred)
        candidates.extend([1, 2])
        tried = set()
        last_err: Optional[Exception] = None
        for ch in candidates:
            if ch in tried:
                continue
            tried.add(ch)
            try:
                self._stream = _try_open(channels=ch)
                self._input_channels = ch
                if self.gui_queue is not None and ch != preferred and preferred > 0:
                    self.gui_queue.put(
                        {
                            "type": "status",
                            "text": f"Status: Audio fallback to {ch} channels (will downmix to mono for STT).",
                        }
                    )
                return
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Audio open failed: {last_err!r}")

    def close(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        finally:
            self._stream = None
            self._pa.terminate()

    def run(self):
        try:
            self._open_stream()
        except Exception as e:
            if self.gui_queue is not None:
                self.gui_queue.put(
                    {"type": "status", "text": f"Audio init failed: {e!r}"}
                )
            return

        frame_ms = self.cfg.frame_ms
        max_frames = int(self.cfg.max_segment_sec * 1000 / frame_ms)
        min_frames = int(self.cfg.min_segment_sec * 1000 / frame_ms)
        silence_trigger_frames = int(self.cfg.vad_silence_ms / frame_ms)

        frames: list[bytes] = []  # will store mono+16k frames
        silence_count = 0

        if self.gui_queue is not None:
            self.gui_queue.put({"type": "status", "text": "Status: Silent Recording"})

        while not self.stop_event.is_set():
            try:
                data = self._stream.read(
                    int(self.cfg.sample_rate * frame_ms / 1000), exception_on_overflow=False
                )
            except Exception:
                # 避免单次 read 异常导致线程退出
                time.sleep(0.05)
                continue

            # 1) Downmix to mono if needed
            width = int(self.cfg.sample_width_bytes)
            in_ch = int(self._input_channels or 1)
            mono = data
            if in_ch == 2:
                # equal weights L/R
                mono = audioop.tomono(data, width, 0.5, 0.5)

            # 2) Resample to 16k if needed (keep state for continuity)
            if int(self.cfg.sample_rate) != int(self._target_sample_rate):
                mono, self._ratecv_state = audioop.ratecv(
                    mono,
                    width,
                    1,  # mono
                    int(self.cfg.sample_rate),
                    int(self._target_sample_rate),
                    self._ratecv_state,
                )

            frames.append(mono)

            rms = _rms_int16(mono)
            if rms < self.cfg.vad_rms_threshold:
                silence_count += 1
            else:
                silence_count = 0

            reached_min = len(frames) >= min_frames
            reached_max = len(frames) >= max_frames
            reached_silence = silence_count >= silence_trigger_frames

            if reached_min and (reached_silence or reached_max):
                wav_bytes = _frames_to_wav_bytes(
                    frames,
                    sample_rate=self._target_sample_rate,
                    channels=1,
                    sample_width_bytes=self.cfg.sample_width_bytes,
                )
                self.stt_queue.put({"wav_bytes": wav_bytes, "ts": time.time()})

                frames = []
                silence_count = 0

        # flush remaining
        if frames:
            wav_bytes = _frames_to_wav_bytes(
                frames,
                sample_rate=self._target_sample_rate,
                channels=1,
                sample_width_bytes=self.cfg.sample_width_bytes,
            )
            self.stt_queue.put({"wav_bytes": wav_bytes, "ts": time.time()})
