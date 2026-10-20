from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint, Signal, QUrl, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QDragEnterEvent, QDropEvent, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSlider,
    QStackedLayout,
)

try:  # QtMultimedia is optional at runtime; static preview still works without it.
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_AVAILABLE = True
except Exception:  # pragma: no cover - depends on local Qt installation
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
    MULTIMEDIA_AVAILABLE = False


VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".ts", ".mts", ".m2ts"}


def app_stylesheet() -> str:
    return """
    QMainWindow, QWidget { background-color: #1a1a1a; color: #e0e0e0; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 12px; }
    QFrame#Panel { background-color: #222222; border: 1px solid #2d2d2d; border-radius: 8px; }
    QFrame#VideoViewport, QWidget#VideoCanvas { background-color: #101010; border: 1px solid #2d2d2d; border-radius: 8px; }
    QWidget#OverlayLayer { background: transparent; border: none; }
    QLabel#SectionTitle { color: #ffffff; font-size: 14px; font-weight: 700; margin-top: 8px; }
    QLabel#Muted { color: #9a9a9a; }
    QLabel#PreviewLabel { color: #909090; background-color: #101010; border: none; }
    QComboBox, QSpinBox { background-color: #2b2b2b; color: #eeeeee; border: 1px solid #3f3f3f; border-radius: 5px; padding: 6px; }
    QComboBox:disabled, QSpinBox:disabled { color: #777777; background-color: #242424; }
    QCheckBox { spacing: 8px; }
    QPlainTextEdit { background-color: #151515; color: #bfbfbf; border: 1px solid #303030; border-radius: 6px; padding: 6px; font-family: Consolas, "Cascadia Mono", monospace; }
    QProgressBar { background-color: #2d2d2d; border-radius: 4px; color: transparent; min-height: 12px; max-height: 12px; border: none; }
    QProgressBar::chunk { background-color: #007acc; border-radius: 4px; }
    QSlider::groove:horizontal { border-radius: 2px; height: 4px; background: #3d3d3d; }
    QSlider::handle:horizontal { background: #007acc; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
    """


class ModernButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, primary: bool = False, danger: bool = False):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        if primary:
            bg, hover, disabled, fg = "#007acc", "#118ad9", "#404040", "white"
        elif danger:
            bg, hover, disabled, fg = "#803333", "#994444", "#333333", "#ffffff"
        else:
            bg, hover, disabled, fg = "#333333", "#444444", "#262626", "#e0e0e0"
        self.setStyleSheet(
            f"""
            QPushButton {{ background-color: {bg}; color: {fg}; border: 1px solid #444444; border-radius: 6px; padding: 9px 14px; font-weight: 600; }}
            QPushButton:hover {{ background-color: {hover}; border-color: #555555; }}
            QPushButton:disabled {{ background-color: {disabled}; color: #707070; border-color: #303030; }}
            """
        )


class SectionTitle(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.setObjectName("SectionTitle")


class InfoLabel(QLabel):
    def __init__(self, text: str = "--"):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setWordWrap(True)


class AspectPixmapLabel(QLabel):
    def __init__(self):
        super().__init__("拖拽视频到此处，或点击“导入视频”")
        self.setObjectName("PreviewLabel")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 320)
        self._source = QPixmap()

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source = QPixmap(pixmap)
        self._refresh_scaled_pixmap()

    def clear_source_pixmap(self) -> None:
        self._source = QPixmap()
        self.clear()
        self.setText("拖拽视频到此处，或点击“导入视频”")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_scaled_pixmap()

    def _refresh_scaled_pixmap(self) -> None:
        if self._source.isNull():
            return
        scaled = self._source.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)


class CropOverlay(QWidget):
    crop_changed = Signal(int, int, int, int)

    HANDLE = 14
    MIN_SIZE = 32

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: transparent; border: none;")
        self.crop_rect = QRect(0, 0, 0, 0)
        self.fixed_ratio = 1.0  # 0 means free ratio.
        self._dragging = False
        self._resizing = False
        self._drag_start = QPoint()
        self._rect_start = QRect()

    def set_fixed_ratio(self, ratio: float) -> None:
        self.fixed_ratio = max(0.0, float(ratio or 0.0))
        self.reset_center()

    def reset_center(self, ratio: float = 0.8) -> None:
        # QPushButton.clicked passes a checked bool; treat it as default ratio
        # instead of accidentally shrinking the crop area to MIN_SIZE.
        if isinstance(ratio, bool):
            ratio = 0.8
        vw, vh = self.width(), self.height()
        if vw <= 0 or vh <= 0:
            return

        target_w = max(self.MIN_SIZE, int(vw * ratio))
        target_h = max(self.MIN_SIZE, int(vh * ratio))
        if self.fixed_ratio > 0:
            target_w, target_h = self._fit_size_to_ratio(target_w, target_h, vw, vh)

        self.set_crop_rect(QRect((vw - target_w) // 2, (vh - target_h) // 2, target_w, target_h))

    def full_crop(self, *_) -> None:
        vw, vh = self.width(), self.height()
        if vw <= 0 or vh <= 0:
            return
        if self.fixed_ratio > 0:
            w, h = self._fit_size_to_ratio(vw, vh, vw, vh)
        else:
            w, h = vw, vh
        self.set_crop_rect(QRect((vw - w) // 2, (vh - h) // 2, w, h))

    def set_crop_rect(self, rect: QRect, *, emit: bool = True) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        w = max(self.MIN_SIZE, min(int(rect.width()), self.width()))
        h = max(self.MIN_SIZE, min(int(rect.height()), self.height()))
        if self.fixed_ratio > 0:
            w, h = self._fit_size_to_ratio(w, h, self.width(), self.height())
        x = min(max(0, int(rect.x())), max(0, self.width() - w))
        y = min(max(0, int(rect.y())), max(0, self.height() - h))
        self.crop_rect = QRect(x, y, w, h)
        self.update()
        if emit:
            self.crop_changed.emit(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        old = event.oldSize()
        if old.isValid() and old.width() > 0 and old.height() > 0 and self.crop_rect.isValid():
            sx = self.width() / old.width()
            sy = self.height() / old.height()
            rect = QRect(
                int(round(self.crop_rect.x() * sx)),
                int(round(self.crop_rect.y() * sy)),
                int(round(self.crop_rect.width() * sx)),
                int(round(self.crop_rect.height() * sy)),
            )
            self.set_crop_rect(rect)
        elif self.width() > 0 and self.height() > 0:
            self.reset_center()

    def _fit_size_to_ratio(self, w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
        ratio = self.fixed_ratio
        if ratio <= 0:
            return max(self.MIN_SIZE, min(w, max_w)), max(self.MIN_SIZE, min(h, max_h))
        w = max(self.MIN_SIZE, min(int(w), max_w))
        h = max(self.MIN_SIZE, min(int(h), max_h))
        if w / h > ratio:
            w = int(h * ratio)
        else:
            h = int(w / ratio)
        if w > max_w:
            w = max_w
            h = int(w / ratio)
        if h > max_h:
            h = max_h
            w = int(h * ratio)
        return max(self.MIN_SIZE, w), max(self.MIN_SIZE, h)

    def _handle_rect(self) -> QRect:
        return QRect(self.crop_rect.right() - self.HANDLE + 1, self.crop_rect.bottom() - self.HANDLE + 1, self.HANDLE, self.HANDLE)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.setFocus(Qt.MouseFocusReason)
        pos = event.position().toPoint()
        self._drag_start = pos
        self._rect_start = QRect(self.crop_rect)

        if self._handle_rect().contains(pos):
            self._resizing = True
            event.accept()
        elif self.crop_rect.contains(pos):
            self._dragging = True
            event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._resizing:
            delta = pos - self._drag_start
            new_w = self._rect_start.width() + delta.x()
            new_h = self._rect_start.height() + delta.y()
            max_w = self.width() - self._rect_start.left()
            max_h = self.height() - self._rect_start.top()
            if self.fixed_ratio > 0:
                new_w, new_h = self._fit_size_to_ratio(new_w, new_h, max_w, max_h)
            else:
                new_w = min(max(self.MIN_SIZE, new_w), max_w)
                new_h = min(max(self.MIN_SIZE, new_h), max_h)
            self.set_crop_rect(QRect(self._rect_start.left(), self._rect_start.top(), int(new_w), int(new_h)))
            return

        if self._dragging:
            rect = QRect(self._rect_start)
            rect.translate(pos - self._drag_start)
            self.set_crop_rect(rect)
            return

        self.setCursor(Qt.SizeFDiagCursor if self._handle_rect().contains(pos) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)

    def keyPressEvent(self, event: QKeyEvent):
        step = 10 if event.modifiers() & Qt.ShiftModifier else 1
        rect = QRect(self.crop_rect)
        key = event.key()
        if key == Qt.Key_Left:
            rect.translate(-step, 0)
        elif key == Qt.Key_Right:
            rect.translate(step, 0)
        elif key == Qt.Key_Up:
            rect.translate(0, -step)
        elif key == Qt.Key_Down:
            rect.translate(0, step)
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            rect.adjust(-step, -step, step, step)
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            rect.adjust(step, step, -step, -step)
        elif key == Qt.Key_R:
            self.reset_center()
            return
        elif key == Qt.Key_F:
            self.full_crop()
            return
        else:
            super().keyPressEvent(event)
            return
        self.set_crop_rect(rect)

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0 or not self.crop_rect.isValid():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(0, 0, 0, 165)))
        painter.setPen(Qt.NoPen)
        r = self.crop_rect
        w, h = self.width(), self.height()

        painter.drawRect(0, 0, w, r.top())
        painter.drawRect(0, r.top(), r.left(), r.height())
        painter.drawRect(r.right() + 1, r.top(), w - r.right() - 1, r.height())
        painter.drawRect(0, r.bottom() + 1, w, h - r.bottom() - 1)

        painter.setPen(QPen(QColor("#00a2ff"), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawRect(r.adjusted(2, 2, -2, -2))

        painter.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.DashLine))
        o_w, o_h = r.width() // 3, r.height() // 3
        painter.drawLine(r.left() + o_w, r.top(), r.left() + o_w, r.bottom())
        painter.drawLine(r.left() + o_w * 2, r.top(), r.left() + o_w * 2, r.bottom())
        painter.drawLine(r.left(), r.top() + o_h, r.right(), r.top() + o_h)
        painter.drawLine(r.left(), r.top() + o_h * 2, r.right(), r.top() + o_h * 2)

        painter.setBrush(QBrush(QColor("#007acc")))
        painter.setPen(QPen(QColor("#e8f4ff"), 1))
        painter.drawEllipse(self._handle_rect().center(), 6, 6)


class VideoViewport(QFrame):
    dropped_file = Signal(str)
    overlay_geometry_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("VideoViewport")
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Keep only the preview/player in the layout. The crop overlay is a direct
        # child of VideoViewport and is positioned manually above video_container.
        # This avoids a subtle QStackedLayout.StackAll z-order issue where the
        # transparent overlay holder could stay below the current preview widget,
        # making the selection rectangle invisible and unclickable on some Qt builds.
        self.video_container = QWidget()
        self.video_container.setObjectName("VideoCanvas")
        self.content_stack = QStackedLayout(self.video_container)
        self.content_stack.setContentsMargins(0, 0, 0, 0)
        self.preview_label = AspectPixmapLabel()
        self.content_stack.addWidget(self.preview_label)

        self.player = None
        self.audio_output = None
        self.video_widget = None
        if MULTIMEDIA_AVAILABLE:
            self.video_widget = QVideoWidget()
            self.content_stack.addWidget(self.video_widget)
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video_widget)
            self.player.positionChanged.connect(self.on_position_changed)
            self.player.durationChanged.connect(self.on_duration_changed)
            self.player.playbackStateChanged.connect(self.on_state_changed)
            self.player.mediaStatusChanged.connect(self.on_media_status_changed)
            if hasattr(self.player, "errorOccurred"):
                self.player.errorOccurred.connect(self.on_player_error)

        layout.addWidget(self.video_container, stretch=1)

        self.overlay = CropOverlay(self)
        self.overlay.setObjectName("OverlayLayer")
        self.overlay.hide()

        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(10, 7, 10, 10)
        self.btn_play = ModernButton("播放")
        self.btn_play.setEnabled(False)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_mode = QLabel("首帧预览")
        self.lbl_mode.setObjectName("Muted")

        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(self.slider)
        ctrl_layout.addWidget(self.lbl_time)
        ctrl_layout.addWidget(self.lbl_mode)
        layout.addLayout(ctrl_layout)

        self.btn_play.clicked.connect(self.toggle_play)
        self.slider.sliderMoved.connect(self.seek)

        self.video_meta_width = 0
        self.video_meta_height = 0
        self._loaded_file = ""
        self._player_ready = False

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._event_has_video(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).suffix.lower() in VIDEO_EXTS:
                self.dropped_file.emit(path)
                event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_overlay_geometry()

    def load_video(self, file_path: str, width: int, height: int, preview: QPixmap | None = None) -> None:
        self.video_meta_width = max(1, int(width))
        self.video_meta_height = max(1, int(height))
        self._loaded_file = file_path
        self._player_ready = False

        if preview is not None and not preview.isNull():
            self.preview_label.set_source_pixmap(preview)
        self.content_stack.setCurrentWidget(self.preview_label)
        self.lbl_mode.setText("首帧预览")

        if self.player is not None:
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.btn_play.setEnabled(True)
            self.slider.setEnabled(True)
        else:
            self.btn_play.setText("播放不可用")
            self.btn_play.setEnabled(False)
            self.slider.setEnabled(False)
            self.lbl_mode.setText("首帧预览 / 当前环境无 QtMultimedia")

        self.overlay.show()
        self._adjust_overlay_geometry(force_reset=True)
        # Run once more after Qt has settled the layout. This is important when
        # the first import happens before the window has received its final size.
        QTimer.singleShot(0, self._adjust_overlay_geometry)

    def toggle_play(self):
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.content_stack.setCurrentWidget(self.video_widget)
            self.lbl_mode.setText("视频预览")
            self._adjust_overlay_geometry()
            self.player.play()

    def seek(self, pos: int) -> None:
        if self.player is not None:
            self.content_stack.setCurrentWidget(self.video_widget)
            self.lbl_mode.setText("视频预览")
            self._adjust_overlay_geometry()
            self.player.setPosition(pos)

    def on_state_changed(self, state):
        self.btn_play.setText("暂停" if state == QMediaPlayer.PlayingState else "播放")

    def on_media_status_changed(self, status):
        if self.player is None:
            return
        loaded_statuses = {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}
        if status in loaded_statuses:
            self._player_ready = True

    def on_player_error(self, error, message: str = ""):
        if message:
            self.lbl_mode.setText("播放器不可用，已保留首帧预览")
        self.content_stack.setCurrentWidget(self.preview_label)

    def on_position_changed(self, pos):
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        self.update_time()

    def on_duration_changed(self, dur):
        self.slider.setRange(0, max(0, dur))
        self.update_time()

    def update_time(self):
        if self.player is None:
            self.lbl_time.setText("00:00 / 00:00")
            return
        p, d = self.player.position() // 1000, self.player.duration() // 1000
        self.lbl_time.setText(f"{p // 60:02d}:{p % 60:02d} / {d // 60:02d}:{d % 60:02d}")

    def clear_preview(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self.preview_label.clear_source_pixmap()
        self.content_stack.setCurrentWidget(self.preview_label)
        self.overlay.hide()
        self.video_meta_width = 0
        self.video_meta_height = 0
        self.btn_play.setText("播放")
        self.btn_play.setEnabled(False)
        self.slider.setEnabled(False)
        self.slider.setRange(0, 0)
        self.lbl_time.setText("00:00 / 00:00")
        self.lbl_mode.setText("首帧预览")

    def _adjust_overlay_geometry(self, *, force_reset: bool = False) -> None:
        if not self.video_meta_width or not self.video_meta_height:
            return
        container_rect = self.video_container.geometry()
        vw, vh = container_rect.width(), container_rect.height()
        if vw <= 0 or vh <= 0:
            return

        scale = min(vw / self.video_meta_width, vh / self.video_meta_height)
        draw_w = max(1, int(self.video_meta_width * scale))
        draw_h = max(1, int(self.video_meta_height * scale))
        x = container_rect.x() + (vw - draw_w) // 2
        y = container_rect.y() + (vh - draw_h) // 2

        self.overlay.setGeometry(x, y, draw_w, draw_h)
        if force_reset or not self.overlay.crop_rect.isValid():
            self.overlay.reset_center()
        else:
            # Clamp stale rectangles after a resize/aspect-ratio change.
            self.overlay.set_crop_rect(self.overlay.crop_rect, emit=False)
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.update()
        self.overlay_geometry_changed.emit()

    @staticmethod
    def _event_has_video(event) -> bool:
        urls = event.mimeData().urls()
        return bool(urls and Path(urls[0].toLocalFile()).suffix.lower() in VIDEO_EXTS)


class MainWindow(QMainWindow):
    about_to_close = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("视界 裁剪工具 Pro")
        self.resize(1240, 800)
        self.setMinimumSize(1000, 640)
        self.setStyleSheet(app_stylesheet())

        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        self.viewport = VideoViewport()
        left_layout.addWidget(self.viewport, stretch=1)

        bottom = QHBoxLayout()
        self.btn_import = ModernButton("导入视频", primary=True)
        self.btn_export = ModernButton("开始导出", primary=True)
        self.btn_cancel = ModernButton("取消", danger=True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QLabel("就绪")

        bottom.addWidget(self.btn_import)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.progress_bar, stretch=1)
        bottom.addWidget(self.status_label)
        left_layout.addLayout(bottom)
        layout.addWidget(left, stretch=7)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("Panel")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(2, 2)
        self.right_panel.setGraphicsEffect(shadow)

        right = QVBoxLayout(self.right_panel)
        right.setContentsMargins(14, 14, 14, 14)
        right.setSpacing(8)

        self.ffmpeg_status = InfoLabel("FFmpeg: 未检测")
        self.btn_choose_ffmpeg = ModernButton("选择 FFmpeg")
        right.addWidget(SectionTitle("环境"))
        right.addWidget(self.ffmpeg_status)
        right.addWidget(self.btn_choose_ffmpeg)

        right.addWidget(SectionTitle("视频信息"))
        self.lbl_filename = InfoLabel("文件名: --")
        self.lbl_resolution = InfoLabel("尺寸: --")
        self.lbl_duration = InfoLabel("时长: --")
        self.lbl_codec = InfoLabel("编码: --")
        self.lbl_audio = InfoLabel("音频: --")
        right.addWidget(self.lbl_filename)
        right.addWidget(self.lbl_resolution)
        right.addWidget(self.lbl_duration)
        right.addWidget(self.lbl_codec)
        right.addWidget(self.lbl_audio)

        right.addWidget(SectionTitle("裁剪"))
        crop_grid = QGridLayout()
        self.lbl_crop_w = InfoLabel("宽度 W: 0 px")
        self.lbl_crop_h = InfoLabel("高度 H: 0 px")
        self.lbl_crop_x = InfoLabel("偏移 X: 0 px")
        self.lbl_crop_y = InfoLabel("偏移 Y: 0 px")
        crop_grid.addWidget(self.lbl_crop_w, 0, 0)
        crop_grid.addWidget(self.lbl_crop_h, 0, 1)
        crop_grid.addWidget(self.lbl_crop_x, 1, 0)
        crop_grid.addWidget(self.lbl_crop_y, 1, 1)
        right.addLayout(crop_grid)

        self.combo_ratio = QComboBox()
        self.combo_ratio.addItem("自由比例", 0.0)
        self.combo_ratio.addItem("1:1 方形", 1.0)
        self.combo_ratio.addItem("16:9 横屏", 16 / 9)
        self.combo_ratio.addItem("9:16 竖屏", 9 / 16)
        self.combo_ratio.addItem("4:3 标准", 4 / 3)
        self.combo_ratio.addItem("3:4 竖版", 3 / 4)
        self.combo_ratio.setCurrentIndex(1)

        crop_buttons = QHBoxLayout()
        self.btn_reset_crop = ModernButton("居中重置")
        self.btn_full_square = ModernButton("最大化")
        crop_buttons.addWidget(self.combo_ratio, stretch=1)
        crop_buttons.addWidget(self.btn_reset_crop)
        crop_buttons.addWidget(self.btn_full_square)
        right.addLayout(crop_buttons)

        right.addWidget(InfoLabel("快捷键：方向键移动，Shift+方向键快速移动，+/- 缩放，R 重置，F 最大化。"))

        right.addWidget(SectionTitle("导出参数"))
        form = QGridLayout()
        self.combo_codec = QComboBox()
        self.combo_codec.addItem("H.264 / x264，兼容性最佳", "libx264")
        self.combo_codec.addItem("H.265 / x265，体积更小", "libx265")
        self.combo_preset = QComboBox()
        for preset in ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"]:
            self.combo_preset.addItem(preset, preset)
        self.combo_preset.setCurrentText("medium")
        self.spin_crf = QSpinBox()
        self.spin_crf.setRange(0, 35)
        self.spin_crf.setValue(17)
        self.spin_crf.setToolTip("数值越小画质越高、文件越大。常用范围：16-23。")
        self.combo_audio = QComboBox()
        self.combo_audio.addItem("复制原音频", "copy")
        self.combo_audio.addItem("转 AAC 192k", "aac")
        self.combo_audio.addItem("不保留音频", "none")
        self.chk_faststart = QCheckBox("MP4 快速启动")
        self.chk_faststart.setChecked(True)
        self.chk_metadata = QCheckBox("保留元数据")
        self.chk_metadata.setChecked(True)

        form.addWidget(QLabel("编码"), 0, 0)
        form.addWidget(self.combo_codec, 0, 1)
        form.addWidget(QLabel("预设"), 1, 0)
        form.addWidget(self.combo_preset, 1, 1)
        form.addWidget(QLabel("CRF"), 2, 0)
        form.addWidget(self.spin_crf, 2, 1)
        form.addWidget(QLabel("音频"), 3, 0)
        form.addWidget(self.combo_audio, 3, 1)
        form.addWidget(self.chk_faststart, 4, 0, 1, 2)
        form.addWidget(self.chk_metadata, 5, 0, 1, 2)
        right.addLayout(form)

        right.addWidget(SectionTitle("日志"))
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.document().setMaximumBlockCount(1200)
        right.addWidget(self.log_box, stretch=1)
        layout.addWidget(self.right_panel, stretch=3)

        self.btn_export.setEnabled(False)
        self.btn_reset_crop.setEnabled(False)
        self.btn_full_square.setEnabled(False)
        self.combo_ratio.setEnabled(False)

    def closeEvent(self, event):
        self.about_to_close.emit(event)

    def append_log(self, text: str):
        self.log_box.appendPlainText(str(text))

    def set_busy(self, busy: bool):
        has_video = self.btn_export.property("has_video") is True
        for widget in (
            self.btn_import,
            self.btn_choose_ffmpeg,
            self.combo_codec,
            self.combo_preset,
            self.spin_crf,
            self.combo_audio,
            self.chk_faststart,
            self.chk_metadata,
            self.combo_ratio,
            self.btn_reset_crop,
            self.btn_full_square,
        ):
            if widget in (self.combo_ratio, self.btn_reset_crop, self.btn_full_square):
                widget.setEnabled((not busy) and has_video)
            else:
                widget.setEnabled(not busy)
        self.btn_export.setEnabled((not busy) and has_video)
        self.btn_cancel.setEnabled(busy)

    def mark_video_loaded(self, loaded: bool):
        self.btn_export.setProperty("has_video", loaded)
        self.btn_export.setEnabled(loaded)
        self.btn_reset_crop.setEnabled(loaded)
        self.btn_full_square.setEnabled(loaded)
        self.combo_ratio.setEnabled(loaded)
        if not loaded:
            self.set_crop_info(None)

    def set_ffmpeg_status(self, text: str, ok: bool):
        color = "#78d47f" if ok else "#ffb35c"
        self.ffmpeg_status.setText(f"<span style='color:{color}'>{html.escape(text)}</span>")

    def set_metadata(self, meta):
        rotation = f"，旋转 {meta.rotation}°" if meta.rotation else ""
        self.lbl_filename.setText(f"文件名: {meta.filename}")
        self.lbl_resolution.setText(
            f"尺寸: {meta.display_width} x {meta.display_height} px（源: {meta.width} x {meta.height}{rotation}）"
        )
        self.lbl_duration.setText(f"时长: {meta.duration_text()} ({meta.fps:g} FPS)，码率: {meta.bit_rate_text()}")
        self.lbl_codec.setText(f"编码: {meta.video_codec} / {meta.pix_fmt}")
        self.lbl_audio.setText(f"音频流: {meta.audio_streams}")

    def clear_metadata(self):
        self.lbl_filename.setText("文件名: --")
        self.lbl_resolution.setText("尺寸: --")
        self.lbl_duration.setText("时长: --")
        self.lbl_codec.setText("编码: --")
        self.lbl_audio.setText("音频: --")
        self.set_crop_info(None)

    def set_crop_info(self, crop):
        if crop is None:
            self.lbl_crop_w.setText("宽度 W: 0 px")
            self.lbl_crop_h.setText("高度 H: 0 px")
            self.lbl_crop_x.setText("偏移 X: 0 px")
            self.lbl_crop_y.setText("偏移 Y: 0 px")
            return
        self.lbl_crop_w.setText(f"宽度 W: {crop.w} px")
        self.lbl_crop_h.setText(f"高度 H: {crop.h} px")
        self.lbl_crop_x.setText(f"偏移 X: {crop.x} px")
        self.lbl_crop_y.setText(f"偏移 Y: {crop.y} px")
