from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox

from .core import (
    CoordinateMapper,
    ExportSettings,
    FFmpegPaths,
    FFmpegWorker,
    FfmpegNotFoundError,
    InvalidCropError,
    VideoAnalyzer,
    find_ffmpeg,
)


VIDEO_FILTER = "视频文件 (*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.flv *.wmv *.ts *.mts *.m2ts);;所有文件 (*.*)"
OUTPUT_FILTER = "MP4 视频 (*.mp4);;MOV 视频 (*.mov);;MKV 视频 (*.mkv);;所有文件 (*.*)"


class VideoCropperController:
    def __init__(self, view):
        self.view = view
        self.settings = QSettings("VideoCropperPro", "VideoCropperPro")
        self.ffmpeg_paths: FFmpegPaths | None = None
        self.input_path: Path | None = None
        self.metadata = None
        self.mapper: CoordinateMapper | None = None
        self.worker: FFmpegWorker | None = None
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None

        self._wire_events()
        self._restore_settings()
        self._setup_ffmpeg()

    def _wire_events(self) -> None:
        self.view.btn_choose_ffmpeg.clicked.connect(self.choose_ffmpeg_folder)
        self.view.btn_import.clicked.connect(self.import_video)
        self.view.btn_export.clicked.connect(self.start_export)
        self.view.btn_cancel.clicked.connect(self.cancel_export)
        self.view.btn_reset_crop.clicked.connect(lambda _checked=False: self.view.viewport.overlay.reset_center())
        self.view.btn_full_square.clicked.connect(lambda _checked=False: self.view.viewport.overlay.full_crop())
        self.view.combo_ratio.currentIndexChanged.connect(lambda _index: self.change_ratio())
        self.view.viewport.dropped_file.connect(self.load_video)
        self.view.viewport.overlay.crop_changed.connect(self.sync_crop)
        self.view.viewport.overlay_geometry_changed.connect(self.sync_from_overlay)
        self.view.about_to_close.connect(self.on_close)

    def _restore_settings(self) -> None:
        self._set_combo_data(self.view.combo_preset, self.settings.value("preset", "medium"))
        self.view.spin_crf.setValue(self._int_setting("crf", 17))
        self._set_combo_data(self.view.combo_audio, self.settings.value("audio_mode", "copy"))
        self._set_combo_data(self.view.combo_codec, self.settings.value("codec", "libx264"))
        self._set_combo_data(self.view.combo_ratio, self._float_setting("ratio", 1.0))
        self.view.chk_faststart.setChecked(self.settings.value("faststart", True, type=bool))
        self.view.chk_metadata.setChecked(self.settings.value("keep_metadata", True, type=bool))

    def _save_settings(self) -> None:
        self.settings.setValue("preset", self.view.combo_preset.currentData())
        self.settings.setValue("crf", self.view.spin_crf.value())
        self.settings.setValue("audio_mode", self.view.combo_audio.currentData())
        self.settings.setValue("codec", self.view.combo_codec.currentData())
        self.settings.setValue("ratio", float(self.view.combo_ratio.currentData() or 0.0))
        self.settings.setValue("faststart", self.view.chk_faststart.isChecked())
        self.settings.setValue("keep_metadata", self.view.chk_metadata.isChecked())

    def _setup_ffmpeg(self, explicit_dir: str | Path | None = None) -> bool:
        explicit_dir = explicit_dir or self.settings.value("ffmpeg_dir", "", type=str) or None
        try:
            self.ffmpeg_paths = find_ffmpeg(explicit_dir)
            self.view.set_ffmpeg_status(f"FFmpeg: {self.ffmpeg_paths.bin_dir}", True)
            self.view.append_log(f"FFmpeg 已就绪: {self.ffmpeg_paths.ffmpeg}")
            return True
        except FfmpegNotFoundError as exc:
            self.ffmpeg_paths = None
            self.view.set_ffmpeg_status(str(exc), False)
            self.view.append_log(str(exc))
            return False

    def choose_ffmpeg_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.view, "选择包含 ffmpeg 与 ffprobe 的文件夹")
        if not folder:
            return
        if self._setup_ffmpeg(folder):
            self.settings.setValue("ffmpeg_dir", folder)
            QMessageBox.information(self.view, "FFmpeg 已配置", f"已使用:\n{folder}")

    def import_video(self) -> None:
        last_dir = self.settings.value("last_dir", str(Path.home()), type=str)
        file_path, _ = QFileDialog.getOpenFileName(self.view, "导入视频", last_dir, VIDEO_FILTER)
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path: str) -> None:
        if not self._ensure_ffmpeg():
            return
        assert self.ffmpeg_paths is not None
        path = Path(file_path)
        try:
            self.view.status_label.setText("正在读取视频...")
            self.view.append_log(f"导入: {path}")

            meta = VideoAnalyzer.get_metadata(path, self.ffmpeg_paths.ffprobe)
            preview = self._make_preview(path)

            self.input_path = meta.path
            self.metadata = meta
            self.mapper = CoordinateMapper(meta.display_width, meta.display_height)

            self.view.viewport.load_video(str(self.input_path), meta.display_width, meta.display_height, preview)
            self.view.set_metadata(meta)
            self.view.mark_video_loaded(True)
            self.change_ratio()
            self.sync_from_overlay()

            self.view.status_label.setText("视频已导入")
            self.settings.setValue("last_dir", str(path.parent))
        except Exception as exc:
            self.input_path = None
            self.metadata = None
            self.mapper = None
            self.view.mark_video_loaded(False)
            self.view.viewport.clear_preview()
            self.view.clear_metadata()
            self.view.status_label.setText("导入失败")
            self.view.append_log(f"导入失败: {exc}")
            QMessageBox.critical(self.view, "导入失败", str(exc))

    def _make_preview(self, path: Path) -> QPixmap:
        assert self.ffmpeg_paths is not None
        self._reset_temp_dir()
        assert self.temp_dir is not None
        preview_path = Path(self.temp_dir.name) / "preview.jpg"
        VideoAnalyzer.extract_preview(path, preview_path, self.ffmpeg_paths.ffmpeg)
        pixmap = QPixmap(str(preview_path))
        if pixmap.isNull():
            raise RuntimeError("预览图加载失败。")
        return pixmap

    def change_ratio(self) -> None:
        ratio = float(self.view.combo_ratio.currentData() or 0.0)
        self.view.viewport.overlay.set_fixed_ratio(ratio)
        self.sync_from_overlay()

    def sync_from_overlay(self) -> None:
        overlay = self.view.viewport.overlay
        self.sync_crop(overlay.crop_rect.x(), overlay.crop_rect.y(), overlay.crop_rect.width(), overlay.crop_rect.height())

    def sync_crop(self, x: int, y: int, w: int, h: int) -> None:
        if not self.mapper:
            return
        overlay = self.view.viewport.overlay
        self.mapper.update_ui_dimensions(overlay.width(), overlay.height())
        try:
            crop = self.mapper.map_to_physical(x, y, w, h)
            self.view.set_crop_info(crop)
        except InvalidCropError as exc:
            self.view.set_crop_info(None)
            self.view.append_log(str(exc))

    def _current_crop(self):
        if not self.mapper:
            raise InvalidCropError("尚未导入视频。")
        overlay = self.view.viewport.overlay
        self.mapper.update_ui_dimensions(overlay.width(), overlay.height())
        return self.mapper.map_to_physical(
            overlay.crop_rect.x(),
            overlay.crop_rect.y(),
            overlay.crop_rect.width(),
            overlay.crop_rect.height(),
        )

    def _export_settings(self) -> ExportSettings:
        return ExportSettings(
            codec=self.view.combo_codec.currentData(),
            preset=self.view.combo_preset.currentData(),
            crf=self.view.spin_crf.value(),
            audio_mode=self.view.combo_audio.currentData(),
            faststart=self.view.chk_faststart.isChecked(),
            keep_metadata=self.view.chk_metadata.isChecked(),
            strip_rotation=True,
        )

    def start_export(self) -> None:
        if not self._ensure_ffmpeg():
            return
        if not self.input_path or not self.metadata:
            QMessageBox.warning(self.view, "未导入视频", "请先导入视频。")
            return

        try:
            crop = self._current_crop()
        except InvalidCropError as exc:
            QMessageBox.warning(self.view, "裁剪区域无效", str(exc))
            return

        suffix = "1x1" if abs(float(self.view.combo_ratio.currentData() or 0.0) - 1.0) < 1e-6 else "crop"
        default_output = self.input_path.with_name(f"{self.input_path.stem}_{suffix}.mp4")
        output_path, _ = QFileDialog.getSaveFileName(self.view, "保存裁剪视频", str(default_output), OUTPUT_FILTER)
        if not output_path:
            return

        output = Path(output_path)
        if not output.suffix:
            output = output.with_suffix(".mp4")

        if os.path.normcase(os.path.abspath(output)) == os.path.normcase(os.path.abspath(self.input_path)):
            QMessageBox.warning(self.view, "输出路径无效", "输出文件不能覆盖输入文件。")
            return

        if output.exists():
            reply = QMessageBox.question(self.view, "确认覆盖", f"文件已存在，是否覆盖？\n{output}")
            if reply != QMessageBox.Yes:
                return

        self._save_settings()
        settings = self._export_settings()
        assert self.ffmpeg_paths is not None
        self.worker = FFmpegWorker(self.ffmpeg_paths.ffmpeg, self.input_path, output, crop, self.metadata.duration, settings)
        self.worker.progress.connect(self.view.progress_bar.setValue)
        self.worker.status.connect(self.view.status_label.setText)
        self.worker.log.connect(self.view.append_log)
        self.worker.finished.connect(self.on_export_finished)
        self.worker.cancelled.connect(self.on_export_cancelled)
        self.worker.error.connect(self.on_export_failed)

        self.view.progress_bar.setValue(0)
        self.view.set_busy(True)
        self.view.status_label.setText("开始导出...")
        self.view.append_log(f"裁剪参数: x={crop.x}, y={crop.y}, w={crop.w}, h={crop.h}")
        self.view.append_log(f"输出: {output}")
        self.worker.start()

    def cancel_export(self) -> None:
        if self.worker and self.worker.isRunning():
            self.view.status_label.setText("正在取消...")
            self.worker.cancel()

    def on_export_finished(self, final_path: str) -> None:
        self.view.set_busy(False)
        self.view.progress_bar.setValue(100)
        self.view.status_label.setText("完成")
        self.view.append_log(f"完成: {final_path}")
        self.worker = None
        QMessageBox.information(self.view, "导出完成", f"已保存至:\n{final_path}")

    def on_export_cancelled(self) -> None:
        self.view.set_busy(False)
        self.view.progress_bar.setValue(0)
        self.view.status_label.setText("已取消")
        self.view.append_log("用户取消了导出任务。")
        self.worker = None

    def on_export_failed(self, message: str) -> None:
        self.view.set_busy(False)
        self.view.status_label.setText("导出失败")
        self.view.append_log(message)
        self.worker = None
        QMessageBox.critical(self.view, "导出失败", message)

    def on_close(self, event) -> None:
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self.view, "确认退出", "导出仍在进行，是否强行退出？") != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_export()
            self.worker.wait(2000)
        self._reset_temp_dir()
        self._save_settings()
        event.accept()

    def _ensure_ffmpeg(self) -> bool:
        if self.ffmpeg_paths or self._setup_ffmpeg():
            return True
        QMessageBox.warning(self.view, "缺少 FFmpeg", "请配置 FFmpeg 后再导入或导出。")
        return False

    def _reset_temp_dir(self) -> None:
        if self.temp_dir is not None:
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass
        self.temp_dir = tempfile.TemporaryDirectory(prefix="video-cropper-preview-")

    @staticmethod
    def _set_combo_data(combo, value) -> None:
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data == value:
                combo.setCurrentIndex(i)
                return
            try:
                if isinstance(data, float) and abs(float(value) - data) < 1e-6:
                    combo.setCurrentIndex(i)
                    return
            except Exception:
                pass
        if isinstance(value, str):
            index = combo.findText(value)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _int_setting(self, key: str, default: int) -> int:
        try:
            return int(self.settings.value(key, default))
        except Exception:
            return default

    def _float_setting(self, key: str, default: float) -> float:
        try:
            return float(self.settings.value(key, default))
        except Exception:
            return default
