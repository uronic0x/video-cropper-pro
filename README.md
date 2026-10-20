# Video Cropper Pro / 视界裁剪工具 Pro

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

A lightweight desktop video cropping tool built with **PySide6 + FFmpeg**. It supports drag-and-drop import, first-frame preview, optional playback preview, multi-aspect-ratio cropping, progress display, and cancellable export.

### Features

- Drag & drop video import with first-frame preview
- Optional real-time playback preview (QtMultimedia), with a reliable static-frame fallback
- Multi-aspect-ratio cropping (1:1, 16:9, 9:16, 4:3, free)
- Keyboard nudging for precise crop-frame adjustment
- Rotation-metadata aware mapping; `rotate` metadata is stripped on export
- Progress bar with cancellable export; input-file overwrite protection

### Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) — `ffmpeg` and `ffprobe` available on the system `PATH` (or select their folder in the UI)

### Run

```bash
pip install -r requirements.txt
python main.py
```

### Project Structure

```
video-cropper-pro/
├── main.py                  # Entry point
├── requirements.txt
└── video_cropper_pro/
    ├── __init__.py          # Version & app constants
    ├── controller.py        # App logic / settings / export control
    ├── core.py              # FFmpeg probing & cropping pipeline
    ├── main_window.py       # Main window assembly
    └── view.py              # UI widgets & crop overlay
```

### License

[MIT](LICENSE)

---

<a name="中文"></a>
## 中文

一个基于 **PySide6 + FFmpeg** 的轻量级桌面视频裁剪工具，支持拖拽导入、首帧预览、可选视频播放预览、多比例裁剪、进度显示和取消导出。

### 功能特性

- 拖拽导入视频，自动生成首帧预览
- 可选 QtMultimedia 实时播放预览，播放组件不可用时回退到稳定的首帧静态预览
- 多比例裁剪（1:1、16:9、9:16、4:3、自由比例）
- 支持键盘微调裁剪框位置
- 正确处理旋转元数据：按显示尺寸映射，导出时清理 `rotate` 元数据，避免二次旋转
- 导出进度显示、可随时取消；输入文件防覆盖校验

### 环境要求

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/)：确保 `ffmpeg` 与 `ffprobe` 在系统 `PATH` 中（也可在界面中手动选择所在文件夹）

### 运行

```bash
pip install -r requirements.txt
python main.py
```

### 项目结构

```
video-cropper-pro/
├── main.py                  # 程序入口
├── requirements.txt
└── video_cropper_pro/
    ├── __init__.py          # 版本与应用常量
    ├── controller.py        # 应用逻辑 / 设置 / 导出控制
    ├── core.py              # FFmpeg 探测与裁剪流程
    ├── main_window.py       # 主窗口组装
    └── view.py              # 界面控件与裁剪覆盖层
```

### 开源协议

[MIT](LICENSE)
