import argparse
import os
import threading
import time
from datetime import datetime
from queue import Queue


def load_config(path: str) -> dict:
    import yaml  # type: ignore
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class AppController:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.gui_queue: Queue = Queue()
        self.stt_queue: Queue = Queue()
        self.stop_event = threading.Event()

        self._threads: list[threading.Thread] = []
        self._active_log_file: str = ""

    def _new_log_file(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"meeting_log_{ts}.txt"
        # 日志写在 teams_assistant 目录（与 main.py 同级），方便定位
        return os.path.abspath(os.path.join(os.path.dirname(__file__), name))

    def get_log_file_path(self) -> str:
        return self._active_log_file

    def start_meeting(self, audio_source: str, sprint_start_hhmm: str, sprint_end_hhmm: str):
        from core.audio_monitor import AudioConfig, AudioMonitorThread
        from core.stt_engine import STTEngineThread
        from core.vision_agent import VisionAgentThread, VisionConfig

        # reset
        self.stop_event.clear()
        self.gui_queue.put({"type": "clear"})
        self._active_log_file = self._new_log_file()
        self.gui_queue.put({"type": "info", "text": f"[Log] {self._active_log_file}"})

        # build audio cfg
        ac = self.cfg.get("audio", {}) or {}
        # UI 选择优先于 config.yaml
        mode = "microphone" if audio_source == "microphone" else "system_loopback"
        audio_cfg = AudioConfig(
            mode=mode,
            device_index=int(ac.get("device_index", 0)),
            sample_rate=int(ac.get("sample_rate", 48000)),
            channels=int(ac.get("channels", 2)),
            sample_width_bytes=int(ac.get("sample_width_bytes", 2)),
            frame_ms=int(ac.get("frame_ms", 30)),
            min_segment_sec=float(ac.get("min_segment_sec", 1.2)),
            max_segment_sec=float(ac.get("max_segment_sec", 5.0)),
            vad_silence_ms=int(ac.get("vad_silence_ms", 650)),
            vad_rms_threshold=float(ac.get("vad_rms_threshold", 180)),
        )

        stt_cfg = self.cfg.get("stt", {}) or {}
        llm_cfg = self.cfg.get("llm", {}) or {}
        vc = self.cfg.get("vision", {}) or {}
        ms = (vc.get("multi_scale", {}) or {})
        vision_cfg = VisionConfig(
            templates_dir=os.path.abspath(os.path.join(os.path.dirname(__file__), vc.get("templates_dir", "assets/templates"))),
            threshold=float(vc.get("threshold", 0.85)),
            interval_sec=int(vc.get("interval_sec", 30)),
            # 视觉侦察时间段从 UI 动态读取
            sprint_monitoring_start=str(sprint_start_hhmm or vc.get("sprint_monitoring_start", "23:50")),
            sprint_monitoring_end=str(sprint_end_hhmm or vc.get("sprint_monitoring_end", "00:30")),
            multi_scale_enabled=bool(ms.get("enabled", True)),
            multi_scales=[float(x) for x in (ms.get("scales", [0.8, 0.9, 1.0, 1.1, 1.2]) or [])],
        )

        def on_exit_found():
            # 视觉线程检测到退出后，触发收尾
            self.gui_queue.put({"type": "status", "text": "Status: Meeting Ended. Finalizing..."})
            self.force_end_and_summarize()

        # audio thread
        audio_worker = AudioMonitorThread(
            cfg=audio_cfg,
            stt_queue=self.stt_queue,
            stop_event=self.stop_event,
            gui_queue=self.gui_queue,
        )
        t_audio = threading.Thread(target=audio_worker.run, name="AudioThread", daemon=True)

        # stt thread
        stt_worker = STTEngineThread(
            stt_cfg=stt_cfg,
            stt_queue=self.stt_queue,
            gui_queue=self.gui_queue,
            stop_event=self.stop_event,
            log_file_path_getter=self.get_log_file_path,
        )
        t_stt = threading.Thread(target=stt_worker.run, name="TranscriptionThread", daemon=True)

        # vision thread
        try:
            vision_worker = VisionAgentThread(
                cfg=vision_cfg,
                gui_queue=self.gui_queue,
                stop_event=self.stop_event,
                on_exit_found=on_exit_found,
            )
            t_vision = threading.Thread(
                target=vision_worker.run, name="VisionThread", daemon=True
            )
        except Exception as e:
            self.gui_queue.put({"type": "status", "text": f"Vision disabled: {e!r}"})
            t_vision = None

        self._threads = [t_audio, t_stt] + ([t_vision] if t_vision else [])
        for t in self._threads:
            t.start()

        self.gui_queue.put({"type": "status", "text": "Status: Silent Recording"})

    def force_end_and_summarize(self):
        # 防止重复触发
        if self.stop_event.is_set():
            return
        self.stop_event.set()

        def _finalize():
            # 等待各线程自然退出（daemon 线程也会随主进程退出，这里尽量做干净收尾）
            for t in self._threads:
                try:
                    t.join(timeout=1.5)
                except Exception:
                    pass

            # 生成会议纪要
            try:
                llm_cfg = self.cfg.get("llm", {}) or {}
                from core.llm_summarizer import LLMSummarizer

                summarizer = LLMSummarizer(llm_cfg)
                out_path = summarizer.generate_summary(self._active_log_file)
                self.gui_queue.put({"type": "done", "path": out_path})
            except Exception as e:
                self.gui_queue.put({"type": "status", "text": f"Summarize error: {e!r}"})

        threading.Thread(target=_finalize, name="Finalization", daemon=True).start()


def run_self_test():
    # 最小自测：验证导入、跨日时间窗口逻辑、GUI 队列写入不崩溃
    from core.vision_agent import is_now_in_time_window
    import datetime as dt

    assert is_now_in_time_window("10:00", "11:00", dt.datetime(2026, 1, 1, 10, 30)) is True
    assert is_now_in_time_window("10:00", "11:00", dt.datetime(2026, 1, 1, 9, 30)) is False
    # 跨日：23:50~00:30
    assert is_now_in_time_window("23:50", "00:30", dt.datetime(2026, 1, 1, 23, 55)) is True
    assert is_now_in_time_window("23:50", "00:30", dt.datetime(2026, 1, 2, 0, 10)) is True
    assert is_now_in_time_window("23:50", "00:30", dt.datetime(2026, 1, 2, 2, 10)) is False

    print("[OK] basic self-test passed.")


def main():
    parser = argparse.ArgumentParser(description="Teams Auto-Assistant")
    parser.add_argument("--config", default="config.yaml", help="config yaml path")
    parser.add_argument("--list-devices", action="store_true", help="list audio devices")
    parser.add_argument("--self-test", action="store_true", help="run basic self test")
    args = parser.parse_args()

    if args.list_devices:
        from core.audio_monitor import list_audio_devices

        devices = list_audio_devices()
        for d in devices:
            if int(d.get("maxInputChannels") or 0) <= 0:
                continue
            print(
                f"[{d['index']}] {d['name']} | in={d['maxInputChannels']} | rate={d['defaultSampleRate']}"
            )
        return

    if args.self_test:
        run_self_test()
        return

    cfg_path = args.config
    # config 相对路径以 main.py 所在目录为基准
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(os.path.dirname(__file__), cfg_path)
    cfg = load_config(cfg_path)

    controller = AppController(cfg)
    ui_cfg = cfg.get("ui", {}) or {}
    from ui.dashboard import Dashboard

    # UI 默认值：从 config.yaml 读取（用户启动时可在面板再改）
    ac = cfg.get("audio", {}) or {}
    vc = cfg.get("vision", {}) or {}
    default_audio = "Microphone/Stereo Mix (Input Device)"
    default_sprint_start = str(vc.get("sprint_monitoring_start", "23:50"))
    default_sprint_end = str(vc.get("sprint_monitoring_end", "00:30"))

    app = Dashboard(
        gui_queue=controller.gui_queue,
        on_start=controller.start_meeting,
        on_force_end=controller.force_end_and_summarize,
        stop_event=controller.stop_event,
        alpha=float(ui_cfg.get("alpha", 0.92)),
        topmost=bool(ui_cfg.get("topmost", True)),
        typing_chars_per_tick=int(ui_cfg.get("typing_chars_per_tick", 12)),
        typing_tick_ms=int(ui_cfg.get("typing_tick_ms", 25)),
        queue_poll_ms=int(ui_cfg.get("queue_poll_ms", 80)),
        default_audio_source=default_audio,
        default_sprint_start=default_sprint_start,
        default_sprint_end=default_sprint_end,
        auto_exit_after_done=bool(ui_cfg.get("auto_exit_after_done", True)),
        auto_exit_delay_ms=int(ui_cfg.get("auto_exit_delay_ms", 900)),
        shutdown_after_done_default=bool(ui_cfg.get("shutdown_after_done_default", False)),
        shutdown_countdown_sec=int(ui_cfg.get("shutdown_countdown_sec", 60)),
    )
    app.mainloop()


if __name__ == "__main__":
    main()
