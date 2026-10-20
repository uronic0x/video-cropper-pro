from __future__ import annotations

import sys
import traceback

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # Friendly console error for source runs without dependencies.
    raise SystemExit(
        "缺少 PySide6。请先执行: pip install -r requirements.txt\n"
        f"原始错误: {exc}"
    ) from exc

from video_cropper_pro import APP_DISPLAY_NAME
from video_cropper_pro.main_window import create_window


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName("VideoCropperPro")

    def excepthook(exc_type, exc_value, exc_tb):
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        QMessageBox.critical(None, "未捕获异常", message)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    window = create_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
