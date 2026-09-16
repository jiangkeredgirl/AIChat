import logging
import struct
import threading
from typing import Callable, Optional

import pyaudio

from config import (
    INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, CHANNELS, CHUNK_SIZE,
    WEBRTC_VAD_ENABLED, WEBRTC_VAD_AGGRESSIVENESS,
    VAD_MIN_SPEECH_MS, VAD_MIN_SILENCE_MS,
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
        # Minimum durations to confirm speech/silence (avoid false triggers)
        self._min_speech_ms = VAD_MIN_SPEECH_MS
        self._min_silence_ms = VAD_MIN_SILENCE_MS

        # WebRTC VAD for human voice detection
        self._vad = None
        if WEBRTC_VAD_ENABLED:
            try:
                import webrtcvad
                self._vad = webrtcvad.Vad(WEBRTC_VAD_AGGRESSIVENESS)
                logger.info(f"WebRTC VAD 已启用 (aggressiveness={WEBRTC_VAD_AGGRESSIVENESS})")
            except Exception as e:
                logger.warning(f"WebRTC VAD 初始化失败，将不过滤音频: {e}")
                self._vad = None

    def _has_human_voice(self, data: bytes) -> bool:
        """Check if audio data contains human voice using WebRTC VAD."""
        if self._vad is None:
            return True  # no VAD, pass everything through
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
        # Require at least 30% of frames to be voice
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
        import time
        now = time.time()

        # Always report energy for monitoring
        if self.on_voice_detected:
            rms = _compute_rms(in_data)
            self.on_voice_detected(rms)

        # Check human voice via WebRTC VAD
        is_speech = self._has_human_voice(in_data)

        if is_speech:
            self._silence_start_time = 0.0
            if not self._speech_confirmed:
                if self._speech_start_time == 0.0:
                    self._speech_start_time = now
                elif (now - self._speech_start_time) * 1000 >= self._min_speech_ms:
                    # Speech confirmed after minimum duration
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
                    # Silence confirmed after minimum duration
                    self._speech_confirmed = False
                    self._was_speech = False
                    self._silence_start_time = 0.0
                    if self.on_speech_state:
                        self.on_speech_state(False)

        # Only send voice audio to API when speech is confirmed
        if self.on_audio_chunk and self._speech_confirmed:
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
