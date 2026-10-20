from __future__ import annotations

from .controller import VideoCropperController
from .view import MainWindow


def create_window() -> MainWindow:
    view = MainWindow()
    # Keep controller referenced by the window to avoid garbage collection.
    view.controller = VideoCropperController(view)
    return view
