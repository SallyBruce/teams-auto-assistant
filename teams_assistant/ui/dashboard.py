import customtkinter as ctk
from queue import Queue, Empty
from threading import Event
from typing import Callable

import re
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox


class Dashboard(ctk.CTk):
    """
    Floating dashboard (runs on the main thread):
    - Top-most + alpha (glass-like window)
    - Real-time transcript textbox (read-only) with auto-scroll
    - UI updates are pulled from a GUI Queue via `.after()` (thread-safe)
    """

    def __init__(
        self,
        gui_queue: Queue,
        on_start: Callable[[str, str, str], None],
        on_force_end: Callable[[], None],
        stop_event: Event,
        *,
        alpha: float = 0.92,
        topmost: bool = True,
        typing_chars_per_tick: int = 12,
        typing_tick_ms: int = 25,
        queue_poll_ms: int = 80,
        default_audio_source: str = "Microphone/Stereo Mix (Input Device)",
        default_sprint_start: str = "22:50",
        default_sprint_end: str = "23:10",
        auto_exit_after_done: bool = True,
        auto_exit_delay_ms: int = 900,
        shutdown_after_done_default: bool = False,
        shutdown_countdown_sec: int = 60,
    ) -> None:
        super().__init__()

        # NOTE: Keep compatibility with older callers that pass Chinese defaults,
        # without rendering any Chinese text in the UI.
        MIC_CN = "\u9ea6\u514b\u98ce"  # "麦克风"
        SYS_CN = "\u7cfb\u7edf\u58f0\u97f3"  # "系统声音"

        # -----------------------
        # Global styling (Modern / Glassmorphism)
        # -----------------------
        ctk.set_appearance_mode("dark")

        self.COLORS = {
            "window_bg": "#070B14",  # deep navy
            "glass_1": "#0B1220",
            "glass_2": "#0F172A",
            "border": "#93C5FD",  # light blue
            "border_soft": "#64748B",  # slate
            "text": "#E5E7EB",
            "text_muted": "#AAB4C4",
            "accent": "#38BDF8",
            "accent_hover": "#60A5FA",
            "danger": "#EF4444",
            "danger_hover": "#F87171",
        }

        # Typography
        self.FONT_TITLE = ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        self.FONT_LABEL = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        self.FONT_BODY = ctk.CTkFont(family="Segoe UI", size=12)
        self.FONT_TRANSCRIPT = ctk.CTkFont(family="Segoe UI", size=13)  # smooth reading

        self.gui_queue = gui_queue
        self.on_start = on_start
        self.on_force_end = on_force_end
        self.stop_event = stop_event

        self.typing_chars_per_tick = max(1, int(typing_chars_per_tick))
        self.typing_tick_ms = max(5, int(typing_tick_ms))
        self.queue_poll_ms = max(20, int(queue_poll_ms))
        self.auto_exit_after_done = bool(auto_exit_after_done)
        self.auto_exit_delay_ms = max(0, int(auto_exit_delay_ms))
        self.shutdown_after_done_default = bool(shutdown_after_done_default)
        self.shutdown_countdown_sec = max(5, int(shutdown_countdown_sec))

        self._pending_text = ""
        self._started = False
        self._closing = False
        self._shutdown_dialog = None
        self._shutdown_remaining = 0

        self.title("Teams Auto-Assistant")
        self.geometry("560x560")
        self.attributes("-topmost", bool(topmost))
        self.attributes("-alpha", float(alpha))
        self.configure(fg_color=self.COLORS["window_bg"])

        # layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=0)

        self.status_label = ctk.CTkLabel(
            self,
            text="Status: Idle",
            anchor="w",
            font=self.FONT_BODY,
            text_color=self.COLORS["text_muted"],
        )
        self.status_label.grid(row=0, column=0, padx=12, pady=(10, 6), sticky="ew")

        self.textbox = ctk.CTkTextbox(
            self,
            wrap="word",
            height=280,
            corner_radius=18,
            border_width=1,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_1"],
            text_color=self.COLORS["text"],
            font=self.FONT_TRANSCRIPT,
        )
        self.textbox.grid(row=1, column=0, padx=12, pady=(0, 10), sticky="nsew")
        self.textbox.configure(state="disabled")

        # -----------------------
        # Configuration Panel
        # -----------------------
        config_frame = ctk.CTkFrame(
            self,
            corner_radius=20,
            border_width=1,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_2"],
        )
        config_frame.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="ew")
        config_frame.grid_columnconfigure(0, weight=0)
        config_frame.grid_columnconfigure(1, weight=1)
        config_frame.grid_columnconfigure(2, weight=0)
        config_frame.grid_columnconfigure(3, weight=1)

        title = ctk.CTkLabel(
            config_frame,
            text="Configuration",
            anchor="w",
            font=self.FONT_TITLE,
            text_color=self.COLORS["text"],
        )
        title.grid(row=0, column=0, columnspan=4, padx=14, pady=(12, 6), sticky="ew")

        # Audio Source
        lbl_audio = ctk.CTkLabel(
            config_frame,
            text="Audio Source",
            anchor="w",
            font=self.FONT_LABEL,
            text_color=self.COLORS["text_muted"],
        )
        lbl_audio.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="w")

        self.audio_source_var = ctk.StringVar(value=default_audio_source)
        self.audio_source_menu = ctk.CTkOptionMenu(
            config_frame,
            values=[
                "Microphone/Stereo Mix (Input Device)",
                "WASAPI Loopback (Output Device)",
            ],
            variable=self.audio_source_var,
            dynamic_resizing=False,
            corner_radius=18,
            fg_color=self.COLORS["glass_1"],
            button_color=self.COLORS["glass_1"],
            button_hover_color=self.COLORS["accent_hover"],
            dropdown_fg_color=self.COLORS["glass_2"],
            dropdown_hover_color=self.COLORS["accent"],
            text_color=self.COLORS["text"],
            font=self.FONT_BODY,
        )
        # Normalize defaults (accept legacy Chinese values)
        d = (default_audio_source or "").strip()
        if (MIC_CN in d) or ("micro" in d.lower()):
            self.audio_source_var.set("Microphone/Stereo Mix (Input Device)")
        elif (SYS_CN in d) or ("loopback" in d.lower()) or ("system" in d.lower()):
            self.audio_source_var.set("WASAPI Loopback (Output Device)")
        else:
            self.audio_source_var.set("Microphone/Stereo Mix (Input Device)")
        self.audio_source_menu.grid(
            row=1, column=1, columnspan=3, padx=14, pady=(0, 10), sticky="ew"
        )

        # Sprint window inputs
        lbl_window = ctk.CTkLabel(
            config_frame,
            text="Sprint Window (Start - End):",
            anchor="w",
            font=self.FONT_LABEL,
            text_color=self.COLORS["text_muted"],
        )
        lbl_window.grid(row=2, column=0, padx=14, pady=(0, 14), sticky="w")

        self.sprint_start_entry = ctk.CTkEntry(
            config_frame,
            placeholder_text="HH:MM",
            corner_radius=18,
            border_width=1,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_1"],
            text_color=self.COLORS["text"],
            font=self.FONT_BODY,
        )
        self.sprint_start_entry.insert(0, default_sprint_start)
        self.sprint_start_entry.grid(
            row=2, column=1, padx=(14, 8), pady=(0, 14), sticky="ew"
        )

        dash = ctk.CTkLabel(
            config_frame,
            text="—",
            anchor="center",
            font=self.FONT_BODY,
            text_color=self.COLORS["text_muted"],
        )
        dash.grid(row=2, column=2, padx=0, pady=(0, 14), sticky="ew")

        self.sprint_end_entry = ctk.CTkEntry(
            config_frame,
            placeholder_text="HH:MM",
            corner_radius=18,
            border_width=1,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_1"],
            text_color=self.COLORS["text"],
            font=self.FONT_BODY,
        )
        self.sprint_end_entry.insert(0, default_sprint_end)
        self.sprint_end_entry.grid(
            row=2, column=3, padx=(8, 14), pady=(0, 14), sticky="ew"
        )

        self.shutdown_after_done_var = tk.BooleanVar(value=self.shutdown_after_done_default)
        self.shutdown_checkbox = ctk.CTkCheckBox(
            config_frame,
            text="Shut down PC after summary (this meeting only)",
            variable=self.shutdown_after_done_var,
            fg_color=self.COLORS["danger"],
            hover_color=self.COLORS["danger_hover"],
            text_color=self.COLORS["text_muted"],
            font=self.FONT_BODY,
        )
        self.shutdown_checkbox.grid(
            row=3, column=0, columnspan=4, padx=14, pady=(0, 14), sticky="w"
        )

        btn_frame = ctk.CTkFrame(
            self,
            corner_radius=20,
            border_width=1,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_2"],
        )
        btn_frame.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="Start Meeting",
            command=self._handle_start,
            corner_radius=18,
            border_width=2,
            border_color=self.COLORS["border"],
            fg_color=self.COLORS["glass_1"],
            hover_color=self.COLORS["accent_hover"],
            text_color=self.COLORS["text"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.btn_start.grid(row=0, column=0, padx=(14, 8), pady=14, sticky="ew")

        self.btn_force = ctk.CTkButton(
            btn_frame,
            text="Force End & Summarize",
            command=self._handle_force_end,
            corner_radius=18,
            border_width=2,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_1"],
            hover_color=self.COLORS["danger_hover"],
            text_color=self.COLORS["text"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.btn_force.grid(row=0, column=1, padx=(8, 14), pady=14, sticky="ew")

        # schedule loops
        self.after(self.queue_poll_ms, self._poll_gui_queue)
        self.after(self.typing_tick_ms, self._flush_pending_text)

        # window close handling
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        # Closing the window stops the app
        if self._closing:
            return
        self._closing = True
        self.stop_event.set()
        try:
            self.destroy()
        except Exception:
            pass

    def _handle_start(self):
        if self._started:
            return
        audio_source = self.get_audio_source()
        start_hhmm, end_hhmm = self.get_sprint_window()
        if not (self._is_valid_hhmm(start_hhmm) and self._is_valid_hhmm(end_hhmm)):
            self.set_status("Status: Invalid time format. Use HH:MM (e.g., 23:50).")
            return

        if bool(self.shutdown_after_done_var.get()):
            ok = messagebox.askyesno(
                "Confirm",
                "This meeting is set to shut down the PC after the summary is saved.\n\nProceed?",
                parent=self,
            )
            if not ok:
                self.shutdown_after_done_var.set(False)

        self._started = True
        self.btn_start.configure(state="disabled")
        self.set_status("Status: Starting...")
        try:
            self.on_start(audio_source, start_hhmm, end_hhmm)
        except Exception as e:
            self.set_status(f"Status: Start failed: {e!r}")
            self._started = False
            self.btn_start.configure(state="normal")

    def _handle_force_end(self):
        self.set_status("Status: Force ending & summarizing...")
        self.on_force_end()

    def set_status(self, text: str):
        self.status_label.configure(text=text)

    def clear_transcript(self):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def _append_text_immediately(self, text: str):
        """低层方法：直接追加并滚动到底部（不做打字机动画）。"""
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def _flush_pending_text(self):
        """
        Typewriter-like experience:
        - STT often returns chunks; we append them in small pieces via `.after()`
        - Always auto-scroll to the bottom for a "live" feeling
        """
        if self._pending_text:
            chunk = self._pending_text[: self.typing_chars_per_tick]
            self._pending_text = self._pending_text[self.typing_chars_per_tick :]
            self._append_text_immediately(chunk)
            self._flash_textbox_border()

        if not self._closing:
            self.after(self.typing_tick_ms, self._flush_pending_text)

    def _poll_gui_queue(self):
        if self._closing:
            return

        try:
            while True:
                msg = self.gui_queue.get_nowait()
                mtype = msg.get("type")
                if mtype == "status":
                    self.set_status(msg.get("text", ""))
                elif mtype == "transcript":
                    # 先进入缓冲，由 _flush_pending_text 以更平滑的方式追加
                    self._pending_text += msg.get("text", "")
                elif mtype == "clear":
                    self.clear_transcript()
                elif mtype == "info":
                    self._pending_text += msg.get("text", "") + "\n"
                elif mtype == "done":
                    self._pending_text += "\n[Summary Saved] " + msg.get("path", "") + "\n"
                    self.set_status("Status: Done")
                    # 允许再次开始下一场会议
                    self._started = False
                    self.btn_start.configure(state="normal")
                    if bool(self.shutdown_after_done_var.get()):
                        self.shutdown_after_done_var.set(self.shutdown_after_done_default)
                        self._start_shutdown_countdown()
                    elif self.auto_exit_after_done:
                        self.after(self.auto_exit_delay_ms, self._on_close)
                else:
                    # 未知消息：忽略
                    pass
        except Empty:
            pass

        if not self._closing:
            self.after(self.queue_poll_ms, self._poll_gui_queue)

    def _start_shutdown_countdown(self):
        if self._closing:
            return
        if self._shutdown_dialog is not None:
            try:
                self._shutdown_dialog.destroy()
            except Exception:
                pass
            self._shutdown_dialog = None

        self._shutdown_remaining = int(self.shutdown_countdown_sec)
        dlg = ctk.CTkToplevel(self)
        self._shutdown_dialog = dlg
        dlg.title("Shutdown Countdown")
        dlg.geometry("420x180")
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=self.COLORS["glass_2"])
        dlg.grid_columnconfigure(0, weight=1)
        dlg.grid_rowconfigure(0, weight=1)
        dlg.grid_rowconfigure(1, weight=0)

        label = ctk.CTkLabel(
            dlg,
            text="PC will shut down after summary is saved.",
            font=self.FONT_LABEL,
            text_color=self.COLORS["text"],
        )
        label.grid(row=0, column=0, padx=18, pady=(18, 6), sticky="ew")

        self._shutdown_counter_label = ctk.CTkLabel(
            dlg,
            text="",
            font=self.FONT_TITLE,
            text_color=self.COLORS["danger"],
        )
        self._shutdown_counter_label.grid(row=0, column=0, padx=18, pady=(54, 6), sticky="ew")

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.grid(row=1, column=0, padx=18, pady=(6, 18), sticky="ew")
        btns.grid_columnconfigure((0, 1), weight=1)

        cancel_btn = ctk.CTkButton(
            btns,
            text="Cancel Shutdown",
            command=self._cancel_shutdown,
            corner_radius=18,
            border_width=2,
            border_color=self.COLORS["border_soft"],
            fg_color=self.COLORS["glass_1"],
            hover_color=self.COLORS["accent_hover"],
            text_color=self.COLORS["text"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        cancel_btn.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="ew")

        now_btn = ctk.CTkButton(
            btns,
            text="Shut Down Now",
            command=self._shutdown_now,
            corner_radius=18,
            border_width=2,
            border_color=self.COLORS["danger"],
            fg_color=self.COLORS["glass_1"],
            hover_color=self.COLORS["danger_hover"],
            text_color=self.COLORS["text"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        now_btn.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="ew")

        try:
            dlg.grab_set()
        except Exception:
            pass

        self._tick_shutdown_countdown()

    def _tick_shutdown_countdown(self):
        if self._shutdown_dialog is None or self._closing:
            return
        if self._shutdown_remaining <= 0:
            self._shutdown_now()
            return
        try:
            self._shutdown_counter_label.configure(text=f"{self._shutdown_remaining}s")
        except Exception:
            pass
        self._shutdown_remaining -= 1
        self.after(1000, self._tick_shutdown_countdown)

    def _cancel_shutdown(self):
        if self._shutdown_dialog is not None:
            try:
                self._shutdown_dialog.destroy()
            except Exception:
                pass
            self._shutdown_dialog = None
        if self.auto_exit_after_done:
            self.after(0, self._on_close)

    def _shutdown_now(self):
        if self._closing:
            return
        if self._shutdown_dialog is not None:
            try:
                self._shutdown_dialog.destroy()
            except Exception:
                pass
            self._shutdown_dialog = None
        self.after(0, self._on_close)
        if sys.platform.startswith("win"):
            try:
                subprocess.Popen(["shutdown", "/s", "/t", "0"], close_fds=True)
            except Exception:
                pass

    def get_audio_source(self) -> str:
        """
        Returns a simplified source id:
        - "microphone"
        - "system"
        """
        v = (self.audio_source_var.get() or "").strip()
        if ("loopback" in v.lower()) or ("wasapi" in v.lower()) or ("output" in v.lower()):
            return "system"
        return "microphone"

    def get_sprint_window(self) -> tuple[str, str]:
        return (self.sprint_start_entry.get().strip(), self.sprint_end_entry.get().strip())

    @staticmethod
    def _is_valid_hhmm(text: str) -> bool:
        text = (text or "").strip()
        if not re.fullmatch(r"\d{2}:\d{2}", text):
            return False
        hh, mm = text.split(":")
        h = int(hh)
        m = int(mm)
        return (0 <= h <= 23) and (0 <= m <= 59)

    def _flash_textbox_border(self):
        """Subtle visual feedback when new transcript text is appended."""
        try:
            self.textbox.configure(border_color=self.COLORS["accent"])
            self.after(
                90,
                lambda: self.textbox.configure(border_color=self.COLORS["border_soft"]),
            )
        except Exception:
            pass
