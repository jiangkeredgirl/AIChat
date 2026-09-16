import asyncio
import base64
import json
import logging
import re
from typing import Callable, Optional

import websockets

from config import (
    WS_URL_TEMPLATE, SYSTEM_INSTRUCTIONS,
    RESPONSE_TIMEOUT_SECONDS, MANUAL_SILENCE_MS,
)

logger = logging.getLogger(__name__)

_SENTENCE_ENDS = re.compile(r'[。！？.!?\n]')


class QwenRealtimeClient:
    """Qwen-Audio Realtime API WebSocket client. Supports server_vad / smart_turn / manual."""

    def __init__(
        self,
        api_key: str,
        workspace_id: str,
        model: str,
        voice: str = "longanqian",
        turn_detection: str = "server_vad",
        on_audio_output: Optional[Callable[[bytes], None]] = None,
        on_user_text: Optional[Callable[[str], None]] = None,
        on_ai_text: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_speech_end: Optional[Callable[[], None]] = None,
        on_response_start: Optional[Callable[[], None]] = None,
        on_response_end: Optional[Callable[[], None]] = None,
    ):
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.model = model
        self.voice = voice
        self.turn_detection = turn_detection
        self.on_audio_output = on_audio_output
        self.on_user_text = on_user_text
        self.on_ai_text = on_ai_text
        self.on_status = on_status
        self.on_error = on_error
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.on_response_start = on_response_start
        self.on_response_end = on_response_end

        self._ws = None
        self._running = False
        self._recv_task = None
        self._cancel_task = None
        self._is_responding = False
        self._cooldown = False
        self._cooldown_task = None
        self._sentence_count = 0
        self._transcript_buffer = ""

    @property
    def is_responding(self) -> bool:
        return self._is_responding

    @property
    def is_manual(self) -> bool:
        return self.turn_detection == "manual"

    def _get_url(self) -> str:
        return WS_URL_TEMPLATE.format(
            workspace_id=self.workspace_id, model=self.model
        )

    def _build_turn_detection_config(self) -> dict | None:
        if self.turn_detection == "server_vad":
            return {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 800,
            }
        elif self.turn_detection == "smart_turn":
            return {"type": "smart_turn"}
        else:  # manual
            return None

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

        session = {
            "modalities": ["text", "audio"],
            "voice": self.voice,
            "instructions": SYSTEM_INSTRUCTIONS,
            "max_history_turns": 5,
            "turn_detection": self._build_turn_detection_config(),
            "input_audio_transcription": {"model": ""},
        }

        await self._ws.send(json.dumps({
            "type": "session.update",
            "session": session,
        }))

        mode_names = {"server_vad": "Server VAD", "smart_turn": "Smart Turn", "manual": "Manual"}
        self._status(f"正在对话...（{mode_names.get(self.turn_detection, self.turn_detection)}）")
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def disconnect(self):
        self._running = False
        self._cancel_timer()
        self._stop_cooldown()
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=2)
            except Exception:
                pass
            self._ws = None
        self._is_responding = False
        self._status("已断开连接")

    async def send_audio(self, pcm_data: bytes):
        if self._ws and self._running:
            await self._ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_data).decode(),
            }))

    async def send_text(self, text: str):
        if self._ws and self._running and text.strip():
            self.reset_interrupt_counters()
            if self._is_responding:
                await self.cancel_response()
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }))
            await self._ws.send(json.dumps({"type": "response.create"}))

    async def send_audio_file(self, pcm_data: bytes):
        if self._ws and self._running:
            self.reset_interrupt_counters()
            if self._is_responding:
                await self.cancel_response()
            chunk_size = 3200
            for i in range(0, len(pcm_data), chunk_size):
                chunk = pcm_data[i:i + chunk_size]
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))
                await asyncio.sleep(0.05)
            await self._ws.send(json.dumps({"type": "response.create"}))

    async def commit_and_respond(self):
        """Manual mode: commit audio buffer and trigger response."""
        if self._ws and self._running:
            self.reset_interrupt_counters()
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await self._ws.send(json.dumps({"type": "response.create"}))
            logger.info("Manual: 已提交音频并请求回复")

    async def cancel_response(self):
        if self._ws and self._running and self._is_responding:
            try:
                await self._ws.send(json.dumps({"type": "response.cancel"}))
                logger.info("已打断 AI 回复")
            except Exception as e:
                logger.error(f"发送 cancel 失败: {e}")

    async def clear_audio_buffer(self):
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
            except Exception:
                pass

    def reset_interrupt_counters(self):
        """Reset timeout and sentence count when new user input arrives."""
        self._cancel_timer()
        self._sentence_count = 0
        self._transcript_buffer = ""
        logger.info("用户新输入，重置超时和句数计数")

    # ── Timeout auto-interrupt ──

    def _start_cancel_timer(self):
        self._cancel_timer()
        self._cancel_task = asyncio.create_task(self._timeout_cancel())

    def _cancel_timer(self):
        if self._cancel_task:
            self._cancel_task.cancel()
            self._cancel_task = None

    async def _timeout_cancel(self):
        try:
            await asyncio.sleep(RESPONSE_TIMEOUT_SECONDS)
            logger.info(f"AI 回复超过 {RESPONSE_TIMEOUT_SECONDS}s，打断")
            await self.cancel_response()
        except asyncio.CancelledError:
            pass

    # ── Cooldown ──

    def _start_cooldown(self):
        self._cooldown = True
        self._stop_cooldown()
        self._cooldown_task = asyncio.create_task(self._cooldown_timer())
        asyncio.create_task(self.clear_audio_buffer())

    def _stop_cooldown(self):
        if self._cooldown_task:
            self._cooldown_task.cancel()
            self._cooldown_task = None

    async def _cooldown_timer(self):
        try:
            await asyncio.sleep(3)
            self._cooldown = False
            logger.info("冷却期结束")
        except asyncio.CancelledError:
            pass

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
            self._is_responding = True
            self._cooldown = False
            self._stop_cooldown()
            self._sentence_count = 0
            self._transcript_buffer = ""
            self._start_cancel_timer()
            if self.on_response_start:
                self.on_response_start()

        elif t == "response.audio.delta":
            audio = base64.b64decode(event["delta"])
            if self.on_audio_output:
                self.on_audio_output(audio)

        elif t == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            if text and self.on_user_text:
                self.on_user_text(text)

        elif t == "response.audio_transcript.done":
            text = event.get("transcript", "")
            if text and self.on_ai_text:
                self.on_ai_text(text)

        elif t == "input_audio_buffer.speech_started":
            if self._cooldown:
                return
            self.reset_interrupt_counters()
            if self.on_speech_start:
                self.on_speech_start()

        elif t == "input_audio_buffer.speech_stopped":
            if self._cooldown:
                return
            if self.on_speech_end:
                self.on_speech_end()

        elif t == "response.done":
            self._is_responding = False
            self._cancel_timer()
            if self.on_response_end:
                self.on_response_end()
            self._start_cooldown()

        elif t == "response.cancelled":
            self._is_responding = False
            self._cancel_timer()
            if self.on_response_end:
                self.on_response_end()
            self._start_cooldown()

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
