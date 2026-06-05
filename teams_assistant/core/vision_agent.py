import datetime as _dt
import os
import time
from dataclasses import dataclass
from queue import Queue
from threading import Event
from typing import Optional


def _parse_hhmm(hhmm: str) -> _dt.time:
    hhmm = (hhmm or "").strip()
    h, m = hhmm.split(":")
    return _dt.time(hour=int(h), minute=int(m))


def is_now_in_time_window(
    start_hhmm: str, end_hhmm: str, now: Optional[_dt.datetime] = None
) -> bool:
    """
    支持跨日窗口：
    - 若 start <= end：同日区间 [start, end]
    - 若 start > end：跨日区间 [start, 24:00) U [00:00, end]
    """
    now = now or _dt.datetime.now()
    start = _parse_hhmm(start_hhmm)
    end = _parse_hhmm(end_hhmm)
    cur = now.time()

    if start <= end:
        return start <= cur <= end
    return (cur >= start) or (cur <= end)


@dataclass
class VisionConfig:
    templates_dir: str
    threshold: float
    interval_sec: int
    sprint_monitoring_start: str
    sprint_monitoring_end: str
    multi_scale_enabled: bool
    multi_scales: list[float]


class VisionAgentThread:
    """
    视觉线程：
    - 仅在冲刺监控时间窗内启动扫描
    - 先侦测“只剩 1 人”的触发特征图（only_one_person.*），避免误挂断
    - 触发后再执行真正的退出动作：优先模板匹配 exit_btn.* 点击，否则发送快捷键兜底
    - 支持多模板、多尺度匹配以提升不同 DPI/缩放兼容性
    """

    def __init__(
        self,
        cfg: VisionConfig,
        gui_queue: Queue,
        stop_event: Event,
        on_exit_found,
    ) -> None:
        self.cfg = cfg
        self.gui_queue = gui_queue
        self.stop_event = stop_event
        self.on_exit_found = on_exit_found

        # 延迟导入第三方库：方便在未安装依赖时也能运行 --self-test
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            import pyautogui  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "视觉模块依赖 opencv-python / numpy / pyautogui，请先安装 requirements.txt。"
            ) from e

        self._cv2 = cv2
        self._np = np
        self._pyautogui = pyautogui

        # 模板分两类：
        # 1) 触发模板：only_one_person.*
        # 2) 退出模板：exit_btn.*
        self._trigger_templates: list[tuple[str, object]] = []
        self._exit_templates: list[tuple[str, object]] = []
        self._load_templates()

    def _load_templates(self):
        if not os.path.isdir(self.cfg.templates_dir):
            return
        for name in os.listdir(self.cfg.templates_dir):
            low = name.lower()
            if not (low.endswith(".png") or low.endswith(".jpg") or low.endswith(".jpeg")):
                continue
            path = os.path.join(self.cfg.templates_dir, name)
            img = self._cv2.imread(path, self._cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            if "only_one_person" in low:
                self._trigger_templates.append((name, img))
            elif "exit_btn" in low:
                self._exit_templates.append((name, img))
            else:
                # 其他图片忽略：避免误用造成误触发
                pass

    def _match_templates(
        self, scr_gray, templates: list[tuple[str, object]]
    ) -> Optional[tuple[int, int, float, str]]:
        """匹配 templates，返回 (x, y, score, template_name) 或 None。"""
        if not templates:
            return None

        best = None  # (score, x, y, tmpl_name)
        for tmpl_name, tmpl_img in templates:
            scales = self.cfg.multi_scales if self.cfg.multi_scale_enabled else [1.0]
            for s in scales:
                if s <= 0:
                    continue
                if s == 1.0:
                    t = tmpl_img
                else:
                    t = self._cv2.resize(
                        tmpl_img,
                        (0, 0),
                        fx=float(s),
                        fy=float(s),
                        interpolation=self._cv2.INTER_AREA,
                    )
                th, tw = t.shape[:2]
                if th < 5 or tw < 5:
                    continue
                if th >= scr_gray.shape[0] or tw >= scr_gray.shape[1]:
                    continue

                res = self._cv2.matchTemplate(scr_gray, t, self._cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = self._cv2.minMaxLoc(res)
                if best is None or max_val > best[0]:
                    x = int(max_loc[0] + tw / 2)
                    y = int(max_loc[1] + th / 2)
                    best = (float(max_val), x, y, tmpl_name)

        if best is None:
            return None
        score, x, y, tmpl_name = best
        if score >= self.cfg.threshold:
            return x, y, score, tmpl_name
        return None

    def scan_for_trigger_only_one_person(self) -> Optional[tuple[int, int, float, str]]:
        """在全屏截图中寻找 only_one_person.*，命中则返回位置与分数。"""
        if not self._trigger_templates:
            return None
        screenshot = self._pyautogui.screenshot()
        scr = self._np.array(screenshot)
        scr_gray = self._cv2.cvtColor(scr, self._cv2.COLOR_RGB2GRAY)
        return self._match_templates(scr_gray, self._trigger_templates)

    def scan_for_exit_button(self) -> Optional[tuple[int, int, float, str]]:
        """在全屏截图中寻找 exit_btn.*，命中则返回位置与分数。"""
        if not self._exit_templates:
            return None
        screenshot = self._pyautogui.screenshot()
        scr = self._np.array(screenshot)
        scr_gray = self._cv2.cvtColor(scr, self._cv2.COLOR_RGB2GRAY)
        return self._match_templates(scr_gray, self._exit_templates)

    def _perform_exit_action(self) -> None:
        """
        执行退出动作：
        1) 优先模板匹配 exit_btn.* 点击
        2) 若匹配不到，使用 Teams 可能支持的快捷键做兜底
        """
        found = self.scan_for_exit_button()
        if found is not None:
            x, y, score, tmpl = found
            self.gui_queue.put(
                {
                    "type": "status",
                    "text": f"Exit button matched ({tmpl}, score={score:.3f}). Clicking...",
                }
            )
            self._pyautogui.moveTo(x, y, duration=0.5)
            self._pyautogui.click()
            return

        # 兜底快捷键（不同 Teams 版本/语言可能不一致）
        self.gui_queue.put(
            {
                "type": "status",
                "text": "Exit button not found. Trying Teams hotkeys fallback...",
            }
        )
        try:
            self._pyautogui.hotkey("alt", "shift", "b")
            time.sleep(0.35)
        except Exception:
            pass
        try:
            self._pyautogui.hotkey("ctrl", "shift", "h")
            time.sleep(0.35)
        except Exception:
            pass

    def run(self):
        while not self.stop_event.is_set():
            in_window = is_now_in_time_window(
                self.cfg.sprint_monitoring_start, self.cfg.sprint_monitoring_end
            )
            if not in_window:
                time.sleep(1.0)
                continue

            self.gui_queue.put(
                {"type": "status", "text": "Status: Watching for only-one-person trigger"}
            )

            try:
                if not self._trigger_templates:
                    self.gui_queue.put(
                        {
                            "type": "status",
                            "text": "Vision: missing trigger template only_one_person.png/jpg. Waiting...",
                        }
                    )
                    time.sleep(max(1, int(self.cfg.interval_sec)))
                    continue

                trigger = self.scan_for_trigger_only_one_person()
            except Exception as e:
                self.gui_queue.put({"type": "status", "text": f"Vision error: {e!r}"})
                time.sleep(max(1, int(self.cfg.interval_sec)))
                continue

            if trigger is not None:
                x, y, score, tmpl = trigger
                self.gui_queue.put(
                    {
                        "type": "status",
                        "text": f"Only-one-person matched ({tmpl}, score={score:.3f}). Exiting meeting...",
                    }
                )
                try:
                    self._perform_exit_action()
                except Exception as e:
                    self.gui_queue.put(
                        {"type": "status", "text": f"Click failed: {e!r}"}
                    )

                # 通知主线程收尾
                self.on_exit_found()
                return

            time.sleep(max(1, int(self.cfg.interval_sec)))
