import asyncio
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext

from config import API_KEY, WORKSPACE_ID, MODELS, VOICES, AUTO_DISCONNECT_TIMEOUT, VOICE_ENERGY_THRESHOLD
from qwen_realtime import QwenRealtimeClient
from audio_handler import AudioHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存设置失败: {e}")


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Qwen-Audio 实时语音聊天")
        self.root.geometry("640x760")
        self.root.resizable(True, True)

        self.loop = None
        self.loop_thread = None
        self.client = None
        self.audio = None
        self.connected = False

        # Auto mode state
        self.auto_mode = tk.BooleanVar(value=True)
        self._last_speech_time = 0.0
        self._voice_active = False
        self._mic_always_on = False
        self._auto_check_id = None
        self._reconnect_pending = False
        self._manual_disconnect = False

        self._build_ui()
        self._load_ui_settings()
        self._center_window()

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _load_ui_settings(self):
        """Load saved settings into UI fields. Priority: settings.json > env vars."""
        s = load_settings()
        api_key = s.get("api_key", "")
        ws_id = s.get("workspace_id", "")
        if api_key:
            self.entry_api_key.delete(0, tk.END)
            self.entry_api_key.insert(0, api_key)
        if ws_id:
            self.entry_ws_id.delete(0, tk.END)
            self.entry_ws_id.insert(0, ws_id)
        model = s.get("model", "")
        if model and model in list(MODELS.keys()):
            self.combo_model.set(model)
        voice = s.get("voice", "")
        if voice and voice in VOICES:
            self.combo_voice.set(voice)

    def _save_ui_settings(self):
        """Save current UI fields to settings.json."""
        save_settings({
            "api_key": self.entry_api_key.get().strip(),
            "workspace_id": self.entry_ws_id.get().strip(),
            "model": self.combo_model.get(),
            "voice": self.combo_voice.get(),
        })

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
        self.combo_voice.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Checkbutton(row2, text="自动模式", variable=self.auto_mode).pack(side=tk.LEFT)

        # --- Control frame ---
        ctrl = ttk.Frame(self.root, padding=4)
        ctrl.pack(fill=tk.X, **pad)

        self.btn_connect = ttk.Button(ctrl, text="连接", command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=4)
        self.btn_disconnect = ttk.Button(ctrl, text="断开", command=self._on_manual_disconnect, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(ctrl, textvariable=self.status_var, foreground="gray").pack(side=tk.LEFT, padx=16)

        self.energy_var = tk.StringVar(value="")
        ttk.Label(ctrl, textvariable=self.energy_var, foreground="#888888").pack(side=tk.RIGHT, padx=8)

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

    # ── Connection ──

    def _on_connect(self):
        api_key = self.entry_api_key.get().strip()
        ws_id = self.entry_ws_id.get().strip()
        if not api_key:
            self._set_status("请输入 API Key")
            return
        if not ws_id:
            self._set_status("请输入 Workspace ID")
            return

        self._manual_disconnect = False
        self._save_ui_settings()
        self._do_connect(api_key, ws_id)

    def _do_connect(self, api_key: str, ws_id: str):
        model_name = self.combo_model.get()
        model_id = MODELS.get(model_name, "qwen-audio-3.0-realtime-plus")
        voice = self.combo_voice.get()

        self.btn_connect.configure(state=tk.DISABLED)
        self._append_chat("system", f"正在连接 {model_name} ...")

        if self.audio is None:
            self.audio = AudioHandler(
                on_audio_chunk=self._on_mic_chunk,
                on_voice_detected=self._on_voice_energy,
            )
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
            on_speech_start=self._on_speech_start,
            on_speech_end=self._on_speech_end,
        )

        if self.loop is None:
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
        self._last_speech_time = time.time()
        self._reconnect_pending = False
        self.root.after(0, self._update_buttons_connected)
        self._append_chat("system", "连接成功，开始对话吧！")
        self._start_auto_check()

    def _update_buttons_connected(self):
        self.btn_connect.configure(state=tk.DISABLED)
        self.btn_disconnect.configure(state=tk.NORMAL)

    def _update_buttons_disconnected(self):
        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)

    # ── Disconnect ──

    def _on_manual_disconnect(self):
        self._manual_disconnect = True
        self._reconnect_pending = False
        self._stop_auto_check()
        self._do_disconnect("手动断开")

    def _do_disconnect(self, reason: str = ""):
        self.connected = False

        if self.client and self.loop:
            try:
                fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
                fut.result(timeout=3)
            except Exception:
                pass

        self.client = None
        self.root.after(0, self._update_buttons_disconnected)
        msg = "已断开连接"
        if reason:
            msg = f"已断开连接（{reason}）"
        self._set_status("未连接")
        self._append_chat("system", msg)

    def _shutdown_audio(self):
        if self.audio:
            try:
                self.audio.stop_microphone()
            except Exception:
                pass
            try:
                self.audio.shutdown()
            except Exception:
                pass
            self.audio = None

    # ── Auto mode ──

    def _on_speech_start(self):
        self._last_speech_time = time.time()
        self._voice_active = True
        self._reconnect_pending = False
        self.root.after(0, lambda: self.energy_var.set("🎤 说话中..."))

    def _on_speech_end(self):
        self._last_speech_time = time.time()
        self._voice_active = False
        self.root.after(0, lambda: self.energy_var.set(""))

    def _on_voice_energy(self, rms: float):
        if not self.auto_mode.get():
            return
        # Show energy level in status bar when not connected
        if not self.connected and self._mic_always_on:
            bar_len = 20
            filled = min(int(rms / 200 * bar_len), bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            self.root.after(0, lambda: self.energy_var.set(f"🎧 监听中 {bar}"))

        # Detect voice when disconnected → auto reconnect
        if not self.connected and self.auto_mode.get() and not self._manual_disconnect:
            if rms > VOICE_ENERGY_THRESHOLD and not self._reconnect_pending:
                self._reconnect_pending = True
                self.root.after(0, self._auto_reconnect)

    def _auto_reconnect(self):
        if self.connected or self._manual_disconnect:
            self._reconnect_pending = False
            return
        api_key = self.entry_api_key.get().strip()
        ws_id = self.entry_ws_id.get().strip()
        if not api_key or not ws_id:
            self._reconnect_pending = False
            return
        self._append_chat("system", "检测到语音，正在自动连接...")
        self._do_connect(api_key, ws_id)

    def _start_auto_check(self):
        """Start periodic check for auto-disconnect."""
        if not self.auto_mode.get():
            return
        self._stop_auto_check()
        self._auto_check_id = self.root.after(2000, self._check_auto_disconnect)

    def _stop_auto_check(self):
        if self._auto_check_id is not None:
            self.root.after_cancel(self._auto_check_id)
            self._auto_check_id = None

    def _check_auto_disconnect(self):
        self._auto_check_id = None
        if not self.connected or not self.auto_mode.get():
            return
        elapsed = time.time() - self._last_speech_time
        remaining = AUTO_DISCONNECT_TIMEOUT - elapsed
        if remaining <= 0:
            self._append_chat("system", f"超过 {AUTO_DISCONNECT_TIMEOUT} 秒未检测到语音，自动断开")
            self._do_disconnect(f"超时 {AUTO_DISCONNECT_TIMEOUT}s 无语音")
            # Start mic monitoring for auto-reconnect
            self._start_mic_monitor()
            return
        if remaining <= 15:
            self._set_status(f"无语音 {int(remaining)}s 后断开")
        self._auto_check_id = self.root.after(2000, self._check_auto_disconnect)

    def _start_mic_monitor(self):
        """Keep mic on for voice detection when disconnected in auto mode."""
        if not self.auto_mode.get():
            return
        self._mic_always_on = True
        if self.audio is None:
            self.audio = AudioHandler(
                on_audio_chunk=self._on_mic_chunk,
                on_voice_detected=self._on_voice_energy,
            )
            self.audio.start_speaker()
        try:
            self.audio.start_microphone()
        except Exception:
            pass
        self._set_status("自动监听中...")

    # ── Callbacks ──

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

    # ── Main loop ──

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self._manual_disconnect = True
        self._stop_auto_check()
        try:
            if self.connected:
                self._do_disconnect()
        except Exception:
            pass
        try:
            self._shutdown_audio()
        except Exception:
            pass
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)


if __name__ == "__main__":
    app = App()
    app.run()
