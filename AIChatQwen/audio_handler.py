import logging
import threading
from typing import Callable, Optional

import pyaudio

from config import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, CHANNELS, CHUNK_SIZE

logger = logging.getLogger(__name__)


class AudioHandler:
    """Handles microphone capture and speaker playback via PyAudio."""

    def __init__(
        self,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
    ):
        self.on_audio_chunk = on_audio_chunk
        self._pa = pyaudio.PyAudio()
        self._mic_stream = None
        self._spk_stream = None
        self._lock = threading.Lock()

    def start_microphone(self):
        if self._mic_stream is not None:
            return
        self._mic_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=INPUT_SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._mic_callback,
        )
        self._mic_stream.start_stream()
        logger.info("麦克风已启动")

    def stop_microphone(self):
        if self._mic_stream is not None:
            self._mic_stream.stop_stream()
            self._mic_stream.close()
            self._mic_stream = None
            logger.info("麦克风已停止")

    def start_speaker(self):
        if self._spk_stream is not None:
            return
        self._spk_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=OUTPUT_SAMPLE_RATE,
            output=True,
        )
        logger.info("扬声器已启动")

    def stop_speaker(self):
        if self._spk_stream is not None:
            self._spk_stream.stop_stream()
            self._spk_stream.close()
            self._spk_stream = None
            logger.info("扬声器已停止")

    def play_audio(self, data: bytes):
        with self._lock:
            if self._spk_stream is not None:
                try:
                    self._spk_stream.write(data)
                except Exception as e:
                    logger.error(f"播放音频错误: {e}")

    def _mic_callback(self, in_data, frame_count, time_info, status):
        if self.on_audio_chunk:
            self.on_audio_chunk(in_data)
        return (None, pyaudio.paContinue)

    def shutdown(self):
        self.stop_microphone()
        self.stop_speaker()
        self._pa.terminate()
