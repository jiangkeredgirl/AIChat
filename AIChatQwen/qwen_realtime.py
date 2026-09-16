import asyncio
import base64
import json
import logging
import re
from typing import Callable, Optional

import websockets

from config import (
    WS_URL_TEMPLATE, SYSTEM_INSTRUCTIONS,
    RESPONSE_INTERRUPT_MODE, RESPONSE_TIMEOUT_SECONDS, RESPONSE_MAX_SENTENCES,
)

logger = logging.getLogger(__name__)

# Sentence-ending punctuation for counting
_SENTENCE_ENDS = re.compile(r'[。！？.!?\n]')


class QwenRealtimeClient:
    """Qwen-Audio Realtime API WebSocket client."""

    def __init__(
        self,
        api_key: str,
        workspace_id: str,
        model: str,
        voice: str = "longanqian",
        on_audio_output: Optional[Callable[[bytes], None]] = None,
        on_user_text: Optional[Callable[[str], None]] = None,
        on_ai_text: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_speech_end: Optional[Callable[[], None]] = None,
        on_response_cancelled: Optional[Callable[[str], None]] = None,
    ):
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.model = model
        self.voice = voice
        self.on_audio_output = on_audio_output
        self.on_user_text = on_user_text
        self.on_ai_text = on_ai_text
        self.on_status = on_status
        self.on_error = on_error
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.on_response_cancelled = on_response_cancelled

        self._ws = None
        self._running = False
        self._recv_task = None
        self._cancel_task = None
        self._transcript_buffer = ""
        self._sentence_count = 0

    def _get_url(self) -> str:
        return WS_URL_TEMPLATE.format(
            workspace_id=self.workspace_id, model=self.model
        )

    async def connect(self):
        url = self._get_url()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        self._status(f"正在连接 {self.model} ...")
        try:
            self._ws = await websockets.connect(url, additional_headers=headers)
        except Exception as e:
            self._error(f"连接失败: {e}")
            return

        self._running = True
        self._status("已连接，正在配置会话...")

        await self._ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": self.voice,
                "instructions": SYSTEM_INSTRUCTIONS,
                "max_history_turns": 5,
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "silence_duration_ms": 800,
                },
                "input_audio_transcription": {
                    "model": ""
                },
            },
        }))

        mode_desc = (
            f"{RESPONSE_TIMEOUT_SECONDS}秒超时"
            if RESPONSE_INTERRUPT_MODE == "timeout"
            else f"超过{RESPONSE_MAX_SENTENCES}句话"
        )
        self._status(f"正在对话...（打断方式: {mode_desc}）")
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def disconnect(self):
        self._running = False
        self._cancel_timer()
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=2)
            except Exception:
                pass
            self._ws = None
        self._status("已断开连接")

    async def send_audio(self, pcm_data: bytes):
        if self._ws and self._running:
            await self._ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_data).decode(),
            }))

    async def cancel_response(self):
        """Send response.cancel to stop the current AI response."""
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps({"type": "response.cancel"}))
                logger.info("已发送 response.cancel")
            except Exception as e:
                logger.error(f"发送 cancel 失败: {e}")

    # ── Interrupt timer / sentence counter ──

    def _start_cancel_timer(self):
        """Start the interrupt mechanism based on configured mode."""
        self._cancel_timer()
        self._transcript_buffer = ""
        self._sentence_count = 0

        if RESPONSE_INTERRUPT_MODE == "timeout":
            self._cancel_task = asyncio.create_task(self._timeout_cancel())
        # "sentences" mode: no timer needed, counted in _handle_event

    def _cancel_timer(self):
        if self._cancel_task:
            self._cancel_task.cancel()
            self._cancel_task = None

    async def _timeout_cancel(self):
        """Auto-cancel after timeout seconds."""
        try:
            await asyncio.sleep(RESPONSE_TIMEOUT_SECONDS)
            logger.info(f"AI 回复超过 {RESPONSE_TIMEOUT_SECONDS}s，自动打断")
            await self.cancel_response()
            if self.on_response_cancelled:
                self.on_response_cancelled(f"超时 {RESPONSE_TIMEOUT_SECONDS}s")
        except asyncio.CancelledError:
            pass

    def _check_sentence_limit(self):
        """Check if sentence limit reached and cancel if so."""
        if RESPONSE_INTERRUPT_MODE != "sentences":
            return
        if self._sentence_count >= RESPONSE_MAX_SENTENCES:
            logger.info(f"AI 回复已达 {self._sentence_count} 句话，自动打断")
            asyncio.create_task(self._cancel_and_notify("sentences"))

    async def _cancel_and_notify(self, reason: str):
        await self.cancel_response()
        if self.on_response_cancelled:
            if reason == "sentences":
                self.on_response_cancelled(f"已达 {RESPONSE_MAX_SENTENCES} 句话")
            else:
                self.on_response_cancelled(f"超时 {RESPONSE_TIMEOUT_SECONDS}s")

    # ── Event handling ──

    async def _recv_loop(self):
        try:
            async for msg in self._ws:
                if not self._running:
                    break
                event = json.loads(msg)
                await self._handle_event(event)
        except websockets.ConnectionClosed:
            if self._running:
                self._error("连接已断开")
                self._running = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._error(f"接收错误: {e}")

    async def _handle_event(self, event: dict):
        t = event.get("type", "")

        if t == "response.created":
            self._start_cancel_timer()

        elif t == "response.audio.delta":
            audio = base64.b64decode(event["delta"])
            if self.on_audio_output:
                self.on_audio_output(audio)

        elif t == "response.audio_transcript.delta":
            # Accumulate transcript and count sentences
            delta = event.get("delta", "")
            self._transcript_buffer += delta
            new_count = len(_SENTENCE_ENDS.findall(self._transcript_buffer))
            if new_count > self._sentence_count:
                self._sentence_count = new_count
                self._check_sentence_limit()

        elif t == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            if text and self.on_user_text:
                self.on_user_text(text)

        elif t == "response.audio_transcript.done":
            text = event.get("transcript", "")
            if text and self.on_ai_text:
                self.on_ai_text(text)

        elif t == "input_audio_buffer.speech_started":
            self._cancel_timer()
            if self.on_speech_start:
                self.on_speech_start()

        elif t == "input_audio_buffer.speech_stopped":
            if self.on_speech_end:
                self.on_speech_end()

        elif t == "response.done":
            self._cancel_timer()

        elif t == "response.cancelled":
            self._cancel_timer()

        elif t == "error":
            self._cancel_timer()
            msg = event.get("error", {}).get("message", "未知错误")
            self._error(f"服务端错误: {msg}")

    def _status(self, msg: str):
        logger.info(msg)
        if self.on_status:
            self.on_status(msg)

    def _error(self, msg: str):
        logger.error(msg)
        if self.on_error:
            self.on_error(msg)
