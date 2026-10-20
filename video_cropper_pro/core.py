from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QThread, Signal


WINDOWS_CREATE_NO_WINDOW = 0x08000000
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200


class FfmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe cannot be located."""


class InvalidCropError(ValueError):
    """Raised when a crop rectangle cannot be encoded safely."""


@dataclass(frozen=True)
class FFmpegPaths:
    ffmpeg: Path
    ffprobe: Path

    @property
    def bin_dir(self) -> Path:
        return self.ffmpeg.parent


@dataclass(frozen=True)
class VideoMetadata:
    path: Path
    filename: str
    width: int
    height: int
    duration: float
    fps: float
    video_codec: str
    pix_fmt: str
    audio_streams: int
    rotation: int = 0
    bit_rate: Optional[int] = None

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    def duration_text(self) -> str:
        total = max(0, int(round(self.duration)))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def bit_rate_text(self) -> str:
        if not self.bit_rate:
            return "--"
        mbps = self.bit_rate / 1_000_000
        if mbps >= 1:
            return f"{mbps:.2f} Mbps"
        return f"{self.bit_rate / 1_000:.0f} kbps"


@dataclass(frozen=True)
class CropParams:
    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    def filter_expr(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y},setsar=1"


@dataclass(frozen=True)
class ExportSettings:
    codec: str = "libx264"
    preset: str = "medium"
    crf: int = 17
    audio_mode: str = "copy"  # copy | aac | none
    faststart: bool = True
    keep_metadata: bool = True
    strip_rotation: bool = True


def _with_hidden_console(*, new_process_group: bool = False) -> dict:
    if os.name != "nt":
        return {}
    flags = WINDOWS_CREATE_NO_WINDOW
    if new_process_group:
        flags |= WINDOWS_CREATE_NEW_PROCESS_GROUP
    return {"creationflags": flags}


def _as_text(value: bytes | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        return float(Fraction(value))
    except Exception:
        return 0.0


def _parse_float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, "N/A", ""):
            return default
        return float(value)
    except Exception:
        return default


def _parse_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, "N/A", ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_even_floor(value: int) -> int:
    value = max(0, int(value))
    return value - (value % 2)


def _candidate_dirs(explicit_dir: str | Path | None = None) -> Iterable[Path]:
    if explicit_dir:
        path = Path(explicit_dir).expanduser()
        if path.is_file():
            yield path.parent
        else:
            yield path
            yield path / "bin"
            yield path / "ffmpeg" / "bin"

    cwd = Path.cwd()
    here = Path(__file__).resolve().parent
    for base in (cwd, here, here.parent, here.parent.parent):
        yield base
        yield base / "bin"
        yield base / "ffmpeg" / "bin"

    import sys

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        bundle = Path(bundle_dir)
        yield bundle
        yield bundle / "bin"
        yield bundle / "ffmpeg" / "bin"


def _is_executable(path: Path) -> bool:
    return path.exists() and path.is_file()


def find_ffmpeg(explicit_dir: str | Path | None = None) -> FFmpegPaths:
    exe = ".exe" if os.name == "nt" else ""
    seen: set[Path] = set()
    for folder in _candidate_dirs(explicit_dir):
        try:
            folder = folder.resolve()
        except Exception:
            continue
        if folder in seen:
            continue
        seen.add(folder)
        ffmpeg = folder / f"ffmpeg{exe}"
        ffprobe = folder / f"ffprobe{exe}"
        if _is_executable(ffmpeg) and _is_executable(ffprobe):
            return FFmpegPaths(ffmpeg=ffmpeg, ffprobe=ffprobe)

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        return FFmpegPaths(ffmpeg=Path(ffmpeg_path).resolve(), ffprobe=Path(ffprobe_path).resolve())

    raise FfmpegNotFoundError(
        "未找到 ffmpeg/ffprobe。请将 ffmpeg 与 ffprobe 放到程序 bin 目录、加入系统 Path，"
        "或在界面中手动选择所在文件夹。"
    )


class VideoAnalyzer:
    @staticmethod
    def get_metadata(video_path: str | Path, ffprobe_path: str | Path) -> VideoMetadata:
        path = Path(video_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"视频文件不存在: {path}")

        cmd = [
            str(ffprobe_path),
            "-hide_banner",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-i",
            str(path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=25,
                check=True,
                **_with_hidden_console(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffprobe 解析超时。") from exc
        except subprocess.CalledProcessError as exc:
            stderr = _as_text(exc.stderr).strip()
            raise RuntimeError(f"FFprobe 解析视频失败: {stderr or '退出码异常'}") from exc

        stdout_text = _as_text(result.stdout).strip()
        if not stdout_text:
            raise RuntimeError("FFprobe 未返回有效数据。")

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FFprobe 返回了无法解析的格式。") from exc

        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video_stream:
            raise ValueError("该文件中未找到视频画面流，请确认是否为纯音频或损坏文件。")

        width = _parse_int(video_stream.get("width"))
        height = _parse_int(video_stream.get("height"))
        if width <= 1 or height <= 1:
            raise ValueError("视频尺寸异常，无法裁剪。")

        fps = _parse_rate(video_stream.get("avg_frame_rate")) or _parse_rate(video_stream.get("r_frame_rate")) or 30.0
        duration = _parse_float(data.get("format", {}).get("duration"))
        if duration <= 0:
            duration = _parse_float(video_stream.get("duration"))
        duration = max(duration, 1.0)

        rotation = 0
        tags = video_stream.get("tags") or {}
        if "rotate" in tags:
            rotation = _parse_int(tags.get("rotate")) % 360
        for item in video_stream.get("side_data_list") or []:
            if "rotation" in item:
                rotation = _parse_int(item.get("rotation")) % 360

        return VideoMetadata(
            path=path.resolve(),
            filename=path.name,
            width=width,
            height=height,
            duration=duration,
            fps=round(fps, 3),
            video_codec=str(video_stream.get("codec_name") or "--"),
            pix_fmt=str(video_stream.get("pix_fmt") or "--"),
            audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
            rotation=rotation,
            bit_rate=_parse_int(data.get("format", {}).get("bit_rate")) or None,
        )

    @staticmethod
    def extract_preview(video_path: str | Path, output_img_path: str | Path, ffmpeg_path: str | Path) -> Path:
        """Extract a first-frame preview. FFmpeg keeps its default autorotate behavior."""
        output = Path(output_img_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()

        cmd = [
            str(ffmpeg_path),
            "-hide_banner",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=True,
                **_with_hidden_console(),
            )
        except subprocess.CalledProcessError as exc:
            stderr = _as_text(exc.stderr).strip()
            raise RuntimeError(f"无法提取预览帧: {stderr or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("提取预览帧超时。") from exc

        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError(f"预览帧生成失败: {result.stderr}")
        return output


class CoordinateMapper:
    def __init__(self, physical_w: int, physical_h: int):
        self.p_w = max(2, int(physical_w))
        self.p_h = max(2, int(physical_h))
        self.ui_w = 1
        self.ui_h = 1

    def update_ui_dimensions(self, ui_w: int, ui_h: int) -> None:
        self.ui_w = max(1, int(ui_w))
        self.ui_h = max(1, int(ui_h))

    def map_to_physical(self, ui_x: int, ui_y: int, ui_box_w: int, ui_box_h: int) -> CropParams:
        scale_x = self.p_w / self.ui_w
        scale_y = self.p_h / self.ui_h

        p_x = int(round(ui_x * scale_x))
        p_y = int(round(ui_y * scale_y))
        p_w = int(round(ui_box_w * scale_x))
        p_h = int(round(ui_box_h * scale_y))

        p_x = min(max(0, p_x), max(0, self.p_w - 2))
        p_y = min(max(0, p_y), max(0, self.p_h - 2))
        p_w = min(max(2, p_w), self.p_w - p_x)
        p_h = min(max(2, p_h), self.p_h - p_y)

        p_x = _safe_even_floor(p_x)
        p_y = _safe_even_floor(p_y)
        p_w = _safe_even_floor(p_w)
        p_h = _safe_even_floor(p_h)

        if p_x + p_w > self.p_w:
            p_w = _safe_even_floor(self.p_w - p_x)
        if p_y + p_h > self.p_h:
            p_h = _safe_even_floor(self.p_h - p_y)

        if p_w < 2 or p_h < 2:
            raise InvalidCropError("裁剪区域过小，无法生成合法的视频编码尺寸。")

        return CropParams(p_x, p_y, p_w, p_h)


def _parse_out_time(value: str) -> float:
    value = value.strip()
    if value.isdigit():
        return int(value) / 1_000_000.0
    match = re.match(r"(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)", value)
    if not match:
        return 0.0
    return int(match.group("h")) * 3600 + int(match.group("m")) * 60 + float(match.group("s"))


class FFmpegWorker(QThread):
    progress = Signal(int)
    status = Signal(str)
    log = Signal(str)
    finished = Signal(str)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, ffmpeg_path, input_path, output_path, crop, total_duration, settings):
        super().__init__()
        self.ffmpeg_path = Path(ffmpeg_path)
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.crop = crop
        self.total_duration = max(float(total_duration or 1.0), 1.0)
        self.settings = settings
        self._cancel_event = threading.Event()
        self._process: Optional[subprocess.Popen[str]] = None
        self._process_lock = threading.Lock()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._terminate_process_tree()

    def _terminate_process_tree(self) -> None:
        with self._process_lock:
            process = self._process
        if not process or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    **_with_hidden_console(),
                )
            else:
                process.terminate()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _build_command(self, temp_output: Path) -> list[str]:
        s = self.settings
        cmd = [
            str(self.ffmpeg_path),
            "-hide_banner",
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(self.input_path),
            "-map",
            "0:v:0",
        ]
        if s.audio_mode in ("copy", "aac"):
            cmd += ["-map", "0:a?"]
        else:
            cmd += ["-an"]

        cmd += [
            "-vf",
            self.crop.filter_expr(),
            "-c:v",
            s.codec,
            "-preset",
            s.preset,
            "-crf",
            str(s.crf),
            "-pix_fmt",
            "yuv420p",
        ]

        if s.codec == "libx265" and temp_output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            cmd += ["-tag:v", "hvc1"]

        if s.audio_mode == "copy":
            cmd += ["-c:a", "copy"]
        elif s.audio_mode == "aac":
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        cmd += ["-map_metadata", "0" if s.keep_metadata else "-1"]
        if s.strip_rotation:
            cmd += ["-metadata:s:v:0", "rotate=0"]
        if s.faststart and temp_output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            cmd += ["-movflags", "+faststart"]

        cmd += ["-progress", "pipe:1", "-nostats", str(temp_output)]
        return cmd

    def run(self) -> None:
        temp_output = self._make_temp_output_path()
        stderr_tail: list[str] = []
        try:
            temp_output.parent.mkdir(parents=True, exist_ok=True)
            if temp_output.exists():
                temp_output.unlink()

            command = self._build_command(temp_output)
            self.log.emit("执行命令: " + subprocess.list2cmdline(command))
            self.status.emit("正在编码...")

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_with_hidden_console(new_process_group=True),
            )
            with self._process_lock:
                self._process = process

            def drain_stderr() -> None:
                assert process.stderr is not None
                for line in process.stderr:
                    line = line.rstrip()
                    if line:
                        stderr_tail.append(line)
                        del stderr_tail[:-80]
                        self.log.emit(line)

            threading.Thread(target=drain_stderr, daemon=True).start()

            assert process.stdout is not None
            while True:
                if self._cancel_event.is_set():
                    self._terminate_process_tree()
                    break
                line = process.stdout.readline()
                if line == "" and process.poll() is not None:
                    break
                if not line:
                    continue

                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms", "out_time"}:
                    seconds = _parse_out_time(value)
                    percent = int(min(99, max(0, seconds / self.total_duration * 100)))
                    self.progress.emit(percent)
                elif key == "progress" and value == "end":
                    self.progress.emit(100)

            return_code = process.wait()
            if self._cancel_event.is_set():
                self._cleanup_temp(temp_output)
                self.status.emit("已取消。")
                self.cancelled.emit()
                return

            if return_code != 0:
                self._cleanup_temp(temp_output)
                detail = "\n".join(stderr_tail[-16:]).strip()
                self.error.emit("FFmpeg 处理失败。" + (f"\n{detail}" if detail else ""))
                return

            if not temp_output.exists() or temp_output.stat().st_size <= 0:
                self._cleanup_temp(temp_output)
                self.error.emit("FFmpeg 未生成有效输出文件。")
                return

            os.replace(temp_output, self.output_path)
            self.progress.emit(100)
            self.status.emit("导出完成。")
            self.finished.emit(str(self.output_path))
        except Exception as exc:
            self._cleanup_temp(temp_output)
            if self._cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.error.emit(f"后台处理异常: {exc}")
        finally:
            with self._process_lock:
                self._process = None

    def _make_temp_output_path(self) -> Path:
        suffix = self.output_path.suffix or ".mp4"
        return self.output_path.with_name(f".{self.output_path.stem}.{uuid.uuid4().hex[:10]}.part{suffix}")

    @staticmethod
    def _cleanup_temp(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
