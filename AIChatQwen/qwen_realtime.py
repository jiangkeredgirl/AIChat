import asyncio
import base64
import json
import logging
from typing import Callable, Optional

import websockets

from config import WS_URL_TEMPLATE

logger = logging.getLogger(__name__)


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

        self._ws = None
        self._running = False
        self._send_task = None
        self._recv_task = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()

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

        self._status("正在对话...")
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def disconnect(self):
        self._running = False
        if self._send_task:
            self._send_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._status("已断开连接")

    async def send_audio(self, pcm_data: bytes):
        if self._ws and self._running:
            await self._ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_data).decode(),
            }))

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

        if t == "response.audio.delta":
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

        elif t == "response.done":
            pass

        elif t == "error":
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
