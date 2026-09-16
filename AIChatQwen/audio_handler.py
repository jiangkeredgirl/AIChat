import logging
import struct
import threading
import queue
import time
from typing import Callable, Optional

import pyaudio

from config import (
    INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, CHANNELS, CHUNK_SIZE,
    WEBRTC_VAD_ENABLED, WEBRTC_VAD_AGGRESSIVENESS,
    VAD_MIN_SPEECH_MS, VAD_MIN_SILENCE_MS, VAD_ENERGY_THRESHOLD,
)

logger = logging.getLogger(__name__)

# WebRTC VAD frame size: 30ms at 16kHz = 480 samples = 960 bytes
_VAD_FRAME_BYTES = 960


def _compute_rms(data: bytes) -> float:
    """Compute RMS energy of 16-bit PCM audio data."""
    n_samples = len(data) // 2
    if n_samples == 0:
        return 0.0
    samples = struct.unpack(f"<{n_samples}h", data)
    sum_sq = sum(s * s for s in samples)
    return (sum_sq / n_samples) ** 0.5


class AudioHandler:
    """Handles microphone capture and speaker playback via PyAudio."""

    def __init__(
        self,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_voice_detected: Optional[Callable[[float], None]] = None,
        on_speech_state: Optional[Callable[[bool], None]] = None,
    ):
        self.on_audio_chunk = on_audio_chunk
        self.on_voice_detected = on_voice_detected
        self.on_speech_state = on_speech_state
        self._pa = pyaudio.PyAudio()
        self._mic_stream = None
        self._spk_stream = None
        self._lock = threading.Lock()
        self._was_speech = False
        self._speech_start_time = 0.0
        self._speech_confirmed = False
        self._silence_start_time = 0.0
        self._min_speech_ms = VAD_MIN_SPEECH_MS
        self._min_silence_ms = VAD_MIN_SILENCE_MS
        # Echo cancellation
        self._last_speaker_write_time = 0.0
        self._echo_tail_ms = 1500
        # Non-blocking audio playback
        self._audio_queue = queue.Queue()
        self._playback_thread = None
        self._playback_running = False

        # WebRTC VAD
        self._vad = None
        if WEBRTC_VAD_ENABLED:
            try:
                import webrtcvad
                self._vad = webrtcvad.Vad(WEBRTC_VAD_AGGRESSIVENESS)
                logger.info(f"WebRTC VAD 已启用 (aggressiveness={WEBRTC_VAD_AGGRESSIVENESS})")
            except Exception as e:
                logger.warning(f"WebRTC VAD 初始化失败: {e}")
                self._vad = None

    def _has_human_voice(self, data: bytes) -> bool:
        if self._vad is None:
            return True
        voice_frames = 0
        total_frames = 0
        offset = 0
        while offset + _VAD_FRAME_BYTES <= len(data):
            frame = data[offset:offset + _VAD_FRAME_BYTES]
            total_frames += 1
            try:
                if self._vad.is_speech(frame, INPUT_SAMPLE_RATE):
                    voice_frames += 1
            except Exception:
                pass
            offset += _VAD_FRAME_BYTES
        if total_frames == 0:
            return False
        return (voice_frames / total_frames) >= 0.3

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
        self._playback_running = True
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()
        logger.info("扬声器已启动")

    def _playback_loop(self):
        """Background thread: plays audio from queue without blocking event loop."""
        while self._playback_running:
            try:
                data = self._audio_queue.get(timeout=0.1)
                if data is None:
                    break
                with self._lock:
                    if self._spk_stream is not None:
                        self._spk_stream.write(data)
                        self._last_speaker_write_time = time.time()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"播放音频错误: {e}")

    def stop_speaker(self):
        self._playback_running = False
        if self._playback_thread:
            self._audio_queue.put(None)
            self._playback_thread.join(timeout=2)
            self._playback_thread = None
        if self._spk_stream is not None:
            self._spk_stream.stop_stream()
            self._spk_stream.close()
            self._spk_stream = None
        logger.info("扬声器已停止")

    def play_audio(self, data: bytes):
        """Non-blocking: queue audio for playback in background thread."""
        self._audio_queue.put(data)

    def clear_playback(self):
        """Clear queued audio (e.g., when interrupting AI response)."""
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _is_echo(self) -> bool:
        return (time.time() - self._last_speaker_write_time) * 1000 < self._echo_tail_ms

    def _mic_callback(self, in_data, frame_count, time_info, status):
        now = time.time()

        rms = _compute_rms(in_data)
        if self.on_voice_detected:
            self.on_voice_detected(rms)

        if rms < VAD_ENERGY_THRESHOLD:
            is_speech = False
        else:
            is_speech = self._has_human_voice(in_data)

        if is_speech and self._is_echo():
            is_speech = False

        if is_speech:
            self._silence_start_time = 0.0
            if not self._speech_confirmed:
                if self._speech_start_time == 0.0:
                    self._speech_start_time = now
                elif (now - self._speech_start_time) * 1000 >= self._min_speech_ms:
                    self._speech_confirmed = True
                    self._was_speech = True
                    if self.on_speech_state:
                        self.on_speech_state(True)
        else:
            self._speech_start_time = 0.0
            if self._speech_confirmed:
                if self._silence_start_time == 0.0:
                    self._silence_start_time = now
                elif (now - self._silence_start_time) * 1000 >= self._min_silence_ms:
                    self._speech_confirmed = False
                    self._was_speech = False
                    self._silence_start_time = 0.0
                    if self.on_speech_state:
                        self.on_speech_state(False)

        # Send audio to server, but block during echo period
        if self.on_audio_chunk and not self._is_echo():
            self.on_audio_chunk(in_data)

        return (None, pyaudio.paContinue)

    def shutdown(self):
        try:
            self.stop_microphone()
        except Exception:
            pass
        try:
            self.stop_speaker()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass
