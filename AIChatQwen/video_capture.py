import logging
import threading
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

logger = logging.getLogger(__name__)


class VideoCapture:
    """Camera capture and display for video chat."""

    def __init__(self, parent, width=320, height=240, fps=15):
        self.width = width
        self.height = height
        self.fps = fps
        self._cap = None
        self._running = False
        self._thread = None
        self._frame = None
        self._lock = threading.Lock()

        # UI
        self.frame = ttk.Frame(parent)
        self.video_label = tk.Label(self.frame, bg="black", width=width, height=height)
        self.video_label.pack(fill=tk.BOTH, expand=True)

    def start(self):
        if self._running:
            return
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            logger.error("无法打开摄像头")
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("摄像头已启动")
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None
        self._frame = None
        self.video_label.configure(image="", text="")
        logger.info("摄像头已停止")

    def get_frame_rgb(self):
        """Get the latest frame as RGB numpy array, or None."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_frame_tk(self):
        """Get the latest frame as PhotoImage for tkinter display."""
        frame = self.get_frame_rgb()
        if frame is None:
            return None
        img = Image.fromarray(frame)
        return ImageTk.PhotoImage(img)

    def _capture_loop(self):
        interval = 1.0 / self.fps
        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                continue
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._lock:
                self._frame = frame_rgb

    def shutdown(self):
        self.stop()
