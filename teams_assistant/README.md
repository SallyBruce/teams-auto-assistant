# Teams Auto-Assistant — 使用指南

> 功能概览与快速开始见仓库根目录 [README.md](../README.md)。

## 默认音源选择与重要提醒（请先读）
- 本项目 UI 默认音源为：Microphone/Stereo Mix (Input Device)
- “Stereo Mix”与“WASAPI Loopback”是两种不同的系统音频捕捉方式，是否可用取决于声卡驱动与 PyAudio 构建；不同电脑可能只有其中一种可用。
- 如果你希望把“同事声音 + 你自己说话”都录进同一个日志文件，常见做法是启用 Windows 的“监听此设备（Listen to this device）”，把麦克风播放到扬声器，从而让 Stereo Mix 同时包含两边声音。该设置会导致你本机听到自己说话的回音，录制结束后建议关闭。

## 你需要准备什么
1. **Python 3.10+**（建议 3.11）
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 复制 `config.local.yaml.example` 为 `config.local.yaml`，填入真实 Key 后运行：
   ```bash
   python main.py --config config.local.yaml
   ```
   （也可直接在 `config.yaml` 填 Key，但不建议提交到 Git。）
4. **准备退出按钮模板图**：
   - 路径：`assets/templates/exit_btn.png`（或放在同目录下的任意 `png/jpg/jpeg`）
   - 建议在与你实际使用 Teams 相同的 **DPI 缩放/主题/语言** 下截取“红色离开按钮”小图（只截按钮本体，不要太大背景）。
   - 你也可以在 `assets/templates/` 放多张 `.png`（不同分辨率/主题），程序会自动逐个匹配并启用多尺度匹配提升兼容性。
5. **准备“只剩 1 人”触发模板图（防误挂断关键）**：
   - 路径：`assets/templates/only_one_person.png`（或 `jpg/jpeg`）
   - 说明：视觉模块会先在你设置的“预计会议可能结束时间段”内每 10 秒截图匹配该图；**只有匹配成功** 才会执行退出（优先点击 `exit_btn.*`，点击不到会尝试快捷键兜底）。

## 如何运行
在 `teams_assistant/` 目录下执行：
```bash
python main.py
```

如使用本地配置文件：
```bash
python main.py --config config.local.yaml
```

### 1) 会议中使用方式
- 点击 **Start Meeting**：开始全程录音转写（低延迟切片 + 简易静音检测）。
  - 会自动创建日志：`meeting_log_YYYYMMDD_HHMMSS.txt`
- 当系统时间进入 `vision.sprint_monitoring_start ~ vision.sprint_monitoring_end`：
  - 视觉线程启动，每 `vision.interval_sec` 秒截屏扫描一次 **only_one_person.\***（避免“离开按钮一直存在”导致误挂断）
  - **只有检测到只剩 1 人** 才会执行退出，并触发收尾
- 你也可以随时点 **Force End & Summarize** 强制结束并生成纪要

### 2) 设备选择（强烈建议先做一次）
列出可用输入设备（用于配置 `audio.device_index`）：
```bash
python main.py --list-devices
```

### 3) 最小自测
```bash
python main.py --self-test
```

## 重要注意事项（Windows / Teams）
1. **系统回环录音**依赖设备/驱动以及 PyAudio 对 WASAPI 的支持：  
   - 若你的环境不支持 loopback，可改用 UI 中的 Microphone/Stereo Mix 方案，并用 `--list-devices` 确认 `device_index`。
2. 如果你使用 “Stereo Mix（Input Device）” 方案录制同事声音：请确保 Teams/Windows 的“扬声器输出设备”与 Stereo Mix 所属声卡一致（例如都走 Realtek Speakers）。如果输出走蓝牙耳机/HDMI 等其它声卡，Stereo Mix 可能录不到同事声音。
3. 若你开启了 Windows 的“监听此设备（Listen to this device）”，这是本机侧音设置：你会听到自己说话的回音。一般不会直接发给远端同事，但在公放音量很大、麦克风离扬声器很近时，仍可能产生回声回灌，建议降低音量并开启 Teams 噪声抑制。
4. 模板匹配对 **DPI 缩放/系统主题/Teams UI 版本** 比较敏感：  
   - 建议准备多张 `exit_btn*.png` 覆盖常见缩放倍率；
   - 已内置多尺度匹配（默认 0.8~1.2）。
5. STT/LLM 统一走 OpenAI Python SDK 的 **OpenAI-compatible** 调用方式：  
   - 在 `config.local.yaml` 填写 `base_url/api_key/model` 即可零代码切换不同平台。

详细架构与配置项说明见 [Teams Auto.md](../Teams%20Auto.md)。
