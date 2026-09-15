import asyncio
import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

from config import API_KEY, WORKSPACE_ID, MODELS, VOICES
from qwen_realtime import QwenRealtimeClient
from audio_handler import AudioHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Qwen-Audio 实时语音聊天")
        self.root.geometry("640x720")
        self.root.resizable(True, True)

        self.loop = None
        self.loop_thread = None
        self.client = None
        self.audio = None
        self.connected = False

        self._build_ui()
        self._center_window()

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- Config frame ---
        cfg = ttk.LabelFrame(self.root, text="配置", padding=8)
        cfg.pack(fill=tk.X, **pad)

        row0 = ttk.Frame(cfg)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="API Key:").pack(side=tk.LEFT)
        self.entry_api_key = ttk.Entry(row0, width=50, show="*")
        self.entry_api_key.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        if API_KEY:
            self.entry_api_key.insert(0, API_KEY)

        row1 = ttk.Frame(cfg)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Workspace ID:").pack(side=tk.LEFT)
        self.entry_ws_id = ttk.Entry(row1, width=50)
        self.entry_ws_id.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        if WORKSPACE_ID:
            self.entry_ws_id.insert(0, WORKSPACE_ID)

        row2 = ttk.Frame(cfg)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="模型:").pack(side=tk.LEFT)
        self.combo_model = ttk.Combobox(row2, values=list(MODELS.keys()), state="readonly", width=20)
        self.combo_model.set("Plus (高质量)")
        self.combo_model.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="音色:").pack(side=tk.LEFT)
        self.combo_voice = ttk.Combobox(row2, values=VOICES, state="readonly", width=16)
        self.combo_voice.set(VOICES[0])
        self.combo_voice.pack(side=tk.LEFT, padx=(4, 0))

        # --- Control frame ---
        ctrl = ttk.Frame(self.root, padding=4)
        ctrl.pack(fill=tk.X, **pad)

        self.btn_connect = ttk.Button(ctrl, text="连接", command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=4)
        self.btn_disconnect = ttk.Button(ctrl, text="断开", command=self._on_disconnect, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(ctrl, textvariable=self.status_var, foreground="gray").pack(side=tk.LEFT, padx=16)

        # --- Conversation frame ---
        conv = ttk.LabelFrame(self.root, text="对话记录", padding=4)
        conv.pack(fill=tk.BOTH, expand=True, **pad)

        self.text_chat = scrolledtext.ScrolledText(
            conv, wrap=tk.WORD, font=("Microsoft YaHei", 10), state=tk.DISABLED
        )
        self.text_chat.pack(fill=tk.BOTH, expand=True)

        self.text_chat.tag_configure("user", foreground="#0078D4", font=("Microsoft YaHei", 10, "bold"))
        self.text_chat.tag_configure("ai", foreground="#107C10", font=("Microsoft YaHei", 10, "bold"))
        self.text_chat.tag_configure("system", foreground="#888888", font=("Microsoft YaHei", 9))

        # --- Status bar ---
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.log_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.log_var, foreground="gray").pack(side=tk.LEFT, padx=8, pady=4)

    def _append_chat(self, role: str, text: str):
        self.text_chat.configure(state=tk.NORMAL)
        if role == "user":
            self.text_chat.insert(tk.END, "🎤 你说: ", "user")
        elif role == "ai":
            self.text_chat.insert(tk.END, "🤖 AI: ", "ai")
        else:
            self.text_chat.insert(tk.END, f"[{role}] ", "system")
        self.text_chat.insert(tk.END, text + "\n\n")
        self.text_chat.see(tk.END)
        self.text_chat.configure(state=tk.DISABLED)

    def _set_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_log(self, text: str):
        self.root.after(0, lambda: self.log_var.set(text))

    def _on_connect(self):
        api_key = self.entry_api_key.get().strip()
        ws_id = self.entry_ws_id.get().strip()
        if not api_key:
            self._set_status("请输入 API Key")
            return
        if not ws_id:
            self._set_status("请输入 Workspace ID")
            return

        model_name = self.combo_model.get()
        model_id = MODELS.get(model_name, "qwen-audio-3.0-realtime-plus")
        voice = self.combo_voice.get()

        self.btn_connect.configure(state=tk.DISABLED)
        self._append_chat("system", f"正在连接 {model_name} ...")

        self.audio = AudioHandler(on_audio_chunk=self._on_mic_chunk)
        self.audio.start_speaker()

        self.client = QwenRealtimeClient(
            api_key=api_key,
            workspace_id=ws_id,
            model=model_id,
            voice=voice,
            on_audio_output=self._on_ai_audio,
            on_user_text=self._on_user_text,
            on_ai_text=self._on_ai_text,
            on_status=self._on_status,
            on_error=self._on_error,
        )

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

        asyncio.run_coroutine_threadsafe(self._connect_async(), self.loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _connect_async(self):
        await self.client.connect()
        self.connected = True
        self.root.after(0, lambda: self.btn_disconnect.configure(state=tk.NORMAL))
        self.audio.start_microphone()
        self._append_chat("system", "连接成功，开始对话吧！")

    def _on_disconnect(self):
        self._append_chat("system", "正在断开...")
        if self.audio:
            self.audio.stop_microphone()
        if self.client and self.loop:
            fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
            fut.result(timeout=5)
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.audio:
            self.audio.shutdown()
            self.audio = None
        self.connected = False
        self.client = None
        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)
        self._set_status("未连接")
        self._append_chat("system", "已断开连接")

    def _on_mic_chunk(self, data: bytes):
        if self.client and self.connected and self.loop:
            asyncio.run_coroutine_threadsafe(self.client.send_audio(data), self.loop)

    def _on_ai_audio(self, data: bytes):
        if self.audio:
            self.audio.play_audio(data)

    def _on_user_text(self, text: str):
        self.root.after(0, lambda: self._append_chat("user", text))

    def _on_ai_text(self, text: str):
        self.root.after(0, lambda: self._append_chat("ai", text))

    def _on_status(self, msg: str):
        self._set_status(msg)
        self._set_log(msg)

    def _on_error(self, msg: str):
        self._set_status(f"错误: {msg}")
        self.root.after(0, lambda: self._append_chat("system", f"错误: {msg}"))

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        if self.connected:
            self._on_disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app = App()
    app.run()
