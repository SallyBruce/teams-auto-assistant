__Teams Auto-Assistant（Teams 会议全自动记录与"察言观色"退出助手）- 产品说明（已同步当前实现）__

> 本文档已根据当前版本代码同步更新（2026-06）。主要变更包括：音源选项重命名（Stereo Mix / WASAPI Loopback）、会后自动关机选项、纪要完成后自动退出、CLI 参数、以及 `config.yaml` 中完整的 UI / 视觉 / 音频参数。

---

## 1. 角色与背景

适用于跨国 IT 会议的桌面端辅助工具，在 Windows + Microsoft Teams 环境下运行。

### 1.1 核心业务目标

1. **会议期间**：后台采集音频并进行实时语音转文字（STT），在悬浮 UI 中以"动态打字 + 自动滚动"的方式展示字幕。
2. **冲刺时间段内**：启动视觉监控，但 **不允许仅凭"离开按钮"就挂断**（Teams 全程都有离开按钮，直接匹配会误挂断）。
3. **察言观色再退出**：当视觉识别到 Teams 界面满足"只剩我一个人"的特征（`only_one_person.*`）后，才执行退出会议动作。
4. **退出后**：停止录音、收尾线程，读取本次会议日志，通过 LLM 生成结构化会议纪要（Markdown）。
5. **会后收尾（可选）**：纪要保存后可自动关闭程序，或按用户勾选执行 PC 关机倒计时。

---

## 2. 技术栈

`requirements.txt` 中的依赖（与当前实现一致）：

| 类别 | 库 | 用途 |
|------|-----|------|
| UI 渲染 | `customtkinter` | 悬浮窗、玻璃拟态风格、置顶 + 透明度 |
| 配置 | `pyyaml` | 读取 `config.yaml` / `config.local.yaml` |
| 音频采集 | `pyaudio` | 麦克风 / WASAPI loopback 采集 |
| 音频打包 | `wave`（标准库） | WAV 封装 |
| 音频降维/重采样 | `audioop`（标准库） | `tomono`、`ratecv` |
| 语音识别 (STT) | `openai` SDK | OpenAI-compatible，可自定义 `base_url` / `api_key` / `model` |
| 视觉与控制 | `pyautogui`、`opencv-python`、`numpy`、`pillow` | 截屏、模板匹配、点击、快捷键兜底 |
| 大语言模型 (LLM) | `openai` SDK | OpenAI-compatible 聊天接口生成纪要 |
| 并发控制 | `threading`、`queue` | 多线程 + 消息队列，严禁阻塞 UI 主线程 |

---

## 3. 架构设计与数据流

采用 **多线程 + 消息队列 (Queue)** 架构，保证 UI 不卡顿。

### 3.1 线程与队列

| 线程 | 职责 |
|------|------|
| **主线程** | 运行 `customtkinter` 的 `mainloop()`；通过 `.after()` 定时轮询 GUI Queue 更新 UI |
| **音频采集线程** | 按 UI 选择的单一音源打开 PyAudio 流，低延迟分帧读取，VAD-like 静音检测切片，推入 STT Queue |
| **转录线程** | 从 STT Queue 取音频片段，调用 OpenAI SDK 转写，追加写入日志文件，推入 GUI Queue |
| **视觉监控线程** | 仅在 Sprint Window 内激活；每隔 `vision.interval_sec` 秒截屏匹配 `only_one_person.*`；匹配成功后才执行退出 |
| **收尾线程** | 视觉触发退出或用户强制结束后：等待工作线程退出 → 调用 LLM 生成 Markdown 纪要 |

### 3.2 GUI Queue 消息类型

| type | 含义 |
|------|------|
| `status` | 更新状态栏文字 |
| `transcript` | 追加字幕（经打字机缓冲后显示） |
| `clear` | 清空字幕区 |
| `info` | 追加信息行（如日志文件路径） |
| `done` | 纪要已保存，附带 `path` 字段；触发会后收尾逻辑 |

### 3.3 防重复触发

`force_end_and_summarize()` 通过 `stop_event` 防止视觉线程与用户手动强制结束重复触发收尾流程。

---

## 4. 目录结构

```
Auto Teams Assistance/
├── Teams Auto.md              # 本文档（产品说明）
└── teams_assistant/
    ├── main.py                # 主程序入口：CLI 参数、AppController、启动 UI
    ├── config.yaml            # 默认配置（STT/LLM/音频/视觉/UI）
    ├── config.local.yaml      # 本地私密配置（.gitignore 忽略，勿提交）
    ├── requirements.txt       # 依赖清单
    ├── README.md              # 快速上手指南
    ├── assets/
    │   └── templates/
    │       ├── exit_btn.png           # Teams 红色退出按钮模板（可多张 png/jpg/jpeg）
    │       └── only_one_person.png    # "只剩 1 人"触发模板（可多张）
    ├── core/
    │   ├── audio_monitor.py   # PyAudio 采集、VAD-like 切片、mono + 16kHz 输出
    │   ├── stt_engine.py      # OpenAI SDK STT 转写
    │   ├── vision_agent.py    # 先匹配 only_one_person，再退出（exit_btn 或快捷键兜底）
    │   └── llm_summarizer.py  # OpenAI SDK 生成会议纪要
    └── ui/
        └── dashboard.py       # 英文界面 + 玻璃拟态 + 配置面板 + 实时字幕
```

运行后会在 `teams_assistant/` 目录生成：

- `meeting_log_YYYYMMDD_HHMMSS.txt` — 会议转写日志
- `Meeting_Summary_YYYYMMDD_HHMMSS.md` — LLM 生成的会议纪要

---

## 5. 核心模块说明

### 5.1 ui/dashboard.py（中控面板）

- **界面语言**：Strictly English UI（所有可见文本与状态提示均为英文）。
- **窗口属性**：置顶（`topmost`）、透明度（`alpha`，玻璃拟态深色主题）。
- **UI 组件**：

  1. **Transcript Textbox**：`CTkTextbox`，`state="disabled"`。通过 `.after()` 从 GUI Queue 取新文字，经打字机缓冲分片追加，强制滚动到底部；追加时边框短暂高亮（`accent` 色闪烁）。
  2. **Status Label**：显示当前系统状态（英文）。
  3. **Configuration Panel**：
     - `Audio Source` 下拉菜单（二选一，禁止混音）：
       - `Microphone/Stereo Mix (Input Device)` — 麦克风或 Stereo Mix 输入设备
       - `WASAPI Loopback (Output Device)` — WASAPI 系统回环（纯系统内声音）
     - `Sprint Window (Start - End):` 两个 `HH:MM` 输入框（支持跨日，启动时校验格式）
     - `Shut down PC after summary (this meeting only)` — 勾选后，本场会议纪要保存完毕将弹出关机倒计时对话框
  4. **Control Panel**：`Start Meeting` 与 `Force End & Summarize` 两个按钮。

- **Start Meeting 行为**：
  - 读取配置面板当前值并传递给底层；Start 后修改下拉框不会自动切换底层音频流（需停止后重新 Start）。
  - 若勾选关机选项，启动前弹出确认对话框。
  - 启动后禁用 Start 按钮，防止重复开始。

- **会后收尾行为**（收到 `done` 消息时）：
  - 字幕区追加 `[Summary Saved] <路径>`
  - 重新启用 Start 按钮
  - 若本场勾选了关机：弹出倒计时对话框（默认 60 秒），可"Cancel Shutdown"或"Shut Down Now"；取消后若 `auto_exit_after_done=true` 则关闭程序
  - 若未勾选关机且 `auto_exit_after_done=true`：延迟 `auto_exit_delay_ms` 后自动关闭窗口

- **关闭窗口**：设置 `stop_event`，停止所有后台线程。

### 5.2 core/audio_monitor.py（音频采集）

- **音频单源**：Start 时根据 UI 选择仅开启一种流，不做双路混音：
  - `microphone`：普通输入流（含 Stereo Mix 输入设备）
  - `system_loopback`：WASAPI loopback（`as_loopback=True`）；若当前 PyAudio 构建不支持则报错提示切换音源

- **通道兜底**：优先使用 `config.yaml` 中 `audio.channels`，再依次尝试 `1`、`2`；若回退为 2 通道，在内存中用 `audioop.tomono` 转单声道，并在状态栏提示。

- **STT 出口强制优化**：发送给 STT 的 WAV 统一为：
  - `channels=1`（mono）
  - `sample_rate=16000Hz`（`audioop.ratecv` 重采样，保持连续状态）
  - `16-bit PCM`

- **切片策略**（可在 `config.yaml` 调整）：
  - `frame_ms`：每次读取帧时长（默认 30ms）
  - `min_segment_sec` / `max_segment_sec`：最短/最长语音片段（默认 2.0s / 6.0s）
  - `vad_silence_ms`：连续静音触发切片（默认 900ms）
  - `vad_rms_threshold`：RMS 能量阈值（默认 110，不同电脑需微调）

- **辅助函数**：`list_audio_devices()` 供 `--list-devices` 列出可用输入设备。

### 5.3 core/stt_engine.py（STT 引擎）

- 使用 `openai` 官方 SDK，`client.audio.transcriptions.create(model=..., file=...)`。
- 支持 OpenAI-compatible 服务（通过 `stt.base_url` / `stt.api_key` / `stt.model` 配置）。
- 每次 Start Meeting 创建新日志 `meeting_log_YYYYMMDD_HHMMSS.txt`，转写文本实时追加，同时推入 GUI Queue。
- 空转写结果跳过，不写入日志。

### 5.4 core/vision_agent.py（视觉雷达）

- **巡逻阶段**（Sprint Window 内，每隔 `interval_sec` 秒）：
  - 仅截屏匹配 `assets/templates/only_one_person.*`
  - **严禁在巡逻阶段直接匹配 exit_btn**（该按钮全程存在，会导致误挂断）
  - 窗口外每秒轮询一次，不执行扫描

- **触发动作**（仅当 `only_one_person.*` 匹配成功）：
  1. 优先匹配并点击 `assets/templates/exit_btn.*`
  2. 匹配不到：依次尝试快捷键兜底 `Alt+Shift+B`、`Ctrl+Shift+H`（不同 Teams 版本可能不同）

- **跨日支持**：`Start > End` 时视为跨日区间（如 `23:50 ~ 00:30`）。

- **多尺度匹配**：`vision.multi_scale` 配置启用后，在 `scales` 列表（默认 0.80~1.20）上逐一缩放模板匹配，提升不同 DPI/主题兼容性。

- **容错**：视觉模块初始化失败时（如缺少 opencv），程序继续运行但禁用视觉线程，不影响录音转写。

### 5.5 core/llm_summarizer.py（纪要生成器）

- `generate_summary(log_file_path)` 读取会议日志全文，调用 LLM 生成 Markdown。
- System Prompt 角色：高级 IT 项目经理。
- 输出结构：
  1. Executive Summary（3~6 条要点）
  2. Key Discussion Points（分主题列点）
  3. Action Items with Assignees（负责人未明确则标注 Owner: TBD）
- 结果保存为 `Meeting_Summary_YYYYMMDD_HHMMSS.md`（时间戳与日志文件一致）。

---

## 6. 关键操作顺序（防误挂断）

在 Sprint Window 内：

1. 视觉线程侦测 `only_one_person.*`（不检测 exit_btn）
2. 匹配成功后执行退出（点击 `exit_btn.*` 或快捷键兜底）
3. 触发收尾：设置 `stop_event` → 等待线程退出 → 调用 LLM 生成会议纪要
4. UI 显示纪要路径，执行会后收尾（自动关闭或关机倒计时）

用户也可随时点击 **Force End & Summarize** 跳过视觉检测，直接强制结束并生成纪要。

---

## 7. 配置说明（config.yaml）

### 7.1 STT / LLM

```yaml
stt:
  api_key: "YOUR_STT_API_KEY"
  base_url: "https://api.siliconflow.cn/v1"    # 可换为任意 OpenAI-compatible 端点
  model: "FunAudioLLM/SenseVoiceSmall"

llm:
  api_key: "YOUR_LLM_API_KEY"
  base_url: "https://api.siliconflow.cn/v1"
  model: "deepseek-ai/DeepSeek-V3"
```

> **安全提示**：不要把真实 API Key 提交到 Git。可将私密配置写入 `config.local.yaml`（已被 `.gitignore` 忽略），运行时指定 `--config config.local.yaml`。

### 7.2 音频

```yaml
audio:
  mode: "system_loopback"       # system_loopback / microphone（UI 选择优先于此项）
  device_index: 11              # 先用 --list-devices 确认
  sample_rate: 48000
  channels: 2
  sample_width_bytes: 2
  frame_ms: 30
  min_segment_sec: 2.0
  max_segment_sec: 6.0
  vad_silence_ms: 900
  vad_rms_threshold: 110
```

### 7.3 视觉

```yaml
vision:
  templates_dir: "assets/templates"
  threshold: 0.85
  interval_sec: 10              # 扫描间隔（秒）
  sprint_monitoring_start: "22:50"   # UI 输入优先；此为启动默认值
  sprint_monitoring_end: "23:10"
  multi_scale:
    enabled: true
    scales: [0.80, 0.90, 1.00, 1.10, 1.20]
```

### 7.4 UI

```yaml
ui:
  alpha: 0.92
  topmost: true
  auto_exit_after_done: true        # 纪要保存后自动关闭程序
  auto_exit_delay_ms: 900           # 自动关闭延迟（毫秒）
  shutdown_after_done_default: false  # 关机复选框默认是否勾选
  shutdown_countdown_sec: 60        # 关机倒计时秒数
  typing_chars_per_tick: 12         # 打字机效果：每次刷新字符数
  typing_tick_ms: 25                # 打字机刷新间隔（毫秒）
  queue_poll_ms: 80                 # GUI Queue 轮询间隔（毫秒）
```

---

## 8. 运行方式

在 `teams_assistant/` 目录下：

```bash
# 安装依赖
pip install -r requirements.txt

# 默认配置启动
python main.py

# 使用本地私密配置
python main.py --config config.local.yaml

# 列出可用音频输入设备（用于确认 device_index）
python main.py --list-devices

# 最小自测（跨日时间窗口逻辑等）
python main.py --self-test
```

### 8.1 会议中使用

1. 在 Configuration 面板选择音源、设置 Sprint Window（预计会议可能结束的时间段）。
2. 可选：勾选"Shut down PC after summary"。
3. 点击 **Start Meeting**：开始全程录音转写，自动创建 `meeting_log_*.txt`。
4. 当系统时间进入 Sprint Window：视觉线程启动，扫描 `only_one_person.*`。
5. 检测到只剩 1 人 → 自动退出会议 → 生成纪要；或随时点 **Force End & Summarize** 手动结束。

### 8.2 音源选择说明

| UI 选项 | 底层 mode | 说明 |
|---------|-----------|------|
| Microphone/Stereo Mix (Input Device) | `microphone` | 使用 PyAudio 输入设备；Stereo Mix 可录系统播放的声音 |
| WASAPI Loopback (Output Device) | `system_loopback` | 通过 WASAPI 回环录纯系统内声音；需 PyAudio 支持 `as_loopback` |

> Stereo Mix 与 WASAPI Loopback 是两种不同的系统音频捕捉方式，是否可用取决于声卡驱动与 PyAudio 构建。

**常见方案：同时录到同事声音和自己说话**

启用 Windows「监听此设备（Listen to this device）」，把麦克风播放到扬声器，让 Stereo Mix 同时包含两边声音。该设置会导致本机听到自己说话的回音，录制结束后建议关闭。

**Stereo Mix 注意事项**：确保 Teams/Windows 的扬声器输出设备与 Stereo Mix 所属声卡一致（如都走 Realtek Speakers）。若输出走蓝牙耳机/HDMI 等其他声卡，Stereo Mix 可能录不到同事声音。

---

## 9. 模板图准备

### 9.1 退出按钮模板（`exit_btn.*`）

- 路径：`assets/templates/exit_btn.png`（或同目录下任意 `png/jpg/jpeg`，文件名含 `exit_btn`）
- 建议在与你实际使用 Teams 相同的 **DPI 缩放 / 主题 / 语言** 下截取红色离开按钮小图（只截按钮本体）。
- 可放多张不同分辨率/主题的图，程序自动逐个匹配并启用多尺度。

### 9.2 "只剩 1 人"触发模板（`only_one_person.*`）

- 路径：`assets/templates/only_one_person.png`（或 `jpg/jpeg`，文件名含 `only_one_person`）
- 在 Sprint Window 内每 `interval_sec` 秒截图匹配；**只有匹配成功** 才会执行退出。
- 这是防误挂断的关键模板，需根据你的 Teams UI 实际状态截取。

---

## 10. 注意事项

1. **系统回环录音**依赖设备/驱动及 PyAudio 对 WASAPI 的支持；若 loopback 不可用，改用 Microphone/Stereo Mix 方案。
2. **模板匹配**对 DPI 缩放、系统主题、Teams UI 版本较敏感；建议准备多张模板覆盖常见缩放倍率。
3. **STT / LLM** 统一走 OpenAI Python SDK 的 OpenAI-compatible 调用；修改 `base_url` / `model` 即可零代码切换平台。
4. **监听此设备**是本机侧音设置，一般不会直接发给远端；但公放音量大、麦克风离扬声器近时仍可能产生回声，建议降低音量并开启 Teams 噪声抑制。
5. 本地生成文件（`meeting_log_*.txt`、`Meeting_Summary_*.md`、`*.wav`）已在 `.gitignore` 中忽略，勿提交到版本库。
