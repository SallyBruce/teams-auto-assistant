import io
import os
import time
from queue import Queue
from threading import Event
from typing import Optional

from openai import OpenAI


class STTEngineThread:
    """转录线程：消费 STT Queue，调用 OpenAI SDK（可配置 base_url）进行语音转文字。"""

    def __init__(
        self,
        stt_cfg: dict,
        stt_queue: Queue,
        gui_queue: Queue,
        stop_event: Event,
        log_file_path_getter,
    ) -> None:
        self.stt_cfg = stt_cfg
        self.stt_queue = stt_queue
        self.gui_queue = gui_queue
        self.stop_event = stop_event
        self.log_file_path_getter = log_file_path_getter

        self._client = OpenAI(
            api_key=stt_cfg.get("api_key", ""),
            base_url=stt_cfg.get("base_url", ""),
        )
        self._model = stt_cfg.get("model", "")

    def _append_log(self, text: str):
        path = self.log_file_path_getter()
        if not path:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

    def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        # OpenAI SDK 要求 file-like。为兼容更多实现，设置 name.
        bio = io.BytesIO(wav_bytes)
        bio.name = "audio.wav"  # type: ignore[attr-defined]

        # 注意：不同平台的 OpenAI-compatible 实现支持的参数可能略有差异。
        # 这里选择尽可能通用的调用方式。
        resp = self._client.audio.transcriptions.create(
            model=self._model,
            file=bio,
        )

        # openai>=1.x 可能返回对象或字符串，做兼容处理
        if isinstance(resp, str):
            return resp
        # 常见：resp.text
        text = getattr(resp, "text", None)
        if text is not None:
            return str(text)
        return str(resp)

    def run(self):
        self.gui_queue.put({"type": "status", "text": "Status: STT Running"})

        while not self.stop_event.is_set():
            try:
                item = self.stt_queue.get(timeout=0.2)
            except Exception:
                continue

            wav_bytes = item.get("wav_bytes")
            if not wav_bytes:
                continue

            try:
                text = self.transcribe_wav_bytes(wav_bytes)
                text = (text or "").strip()
                if not text:
                    continue

                # 追加换行，保证日志与 UI 一致可读
                stamped = f"{text}\n"
                self._append_log(stamped)
                self.gui_queue.put({"type": "transcript", "text": stamped})
            except Exception as e:
                self.gui_queue.put({"type": "status", "text": f"STT error: {e!r}"})
                time.sleep(0.2)

