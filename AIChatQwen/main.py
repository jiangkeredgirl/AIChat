import asyncio
import json
import logging
import os
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog

from config import (
    API_KEY, WORKSPACE_ID, MODELS, OMNI_MODELS,
    VOICES_AUDIO, VOICES_OMNI,
    AUTO_DISCONNECT_TIMEOUT, VOICE_ENERGY_THRESHOLD,
    TURN_DETECTION_MODES, MANUAL_SILENCE_MS,
)
from qwen_realtime import QwenRealtimeClient
from audio_handler import AudioHandler

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

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
        self.root.geometry("700x850")
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

        # Manual turn control
        self._user_speaking = False
        self._silence_check_id = None

        # Video state
        self.video_capture = None
        self._video_showing = False
        self._video_after_id = None

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
        # Update voice list based on model, then restore saved voice
        self._update_voice_list()
        voice = s.get("voice", "")
        voices = self._current_voices()
        if voice and voice in voices:
            self.combo_voice.set(voice)
        turn = s.get("turn_detection", "")
        if turn and turn in list(TURN_DETECTION_MODES.keys()):
            self.combo_turn.set(turn)

    def _current_voices(self):
        """Return the voice list for the currently selected model."""
        model_id = MODELS.get(self.combo_model.get(), "")
        if model_id in OMNI_MODELS:
            return VOICES_OMNI
        return VOICES_AUDIO

    def _update_voice_list(self):
        """Update voice combobox values based on selected model."""
        voices = self._current_voices()
        self.combo_voice.configure(values=voices)
        if self.combo_voice.get() not in voices:
            self.combo_voice.set(voices[0])

    def _on_model_changed(self, event=None):
        """Called when model selection changes."""
        self._update_voice_list()

    def _save_ui_settings(self):
        save_settings({
            "api_key": self.entry_api_key.get().strip(),
            "workspace_id": self.entry_ws_id.get().strip(),
            "model": self.combo_model.get(),
            "voice": self.combo_voice.get(),
            "turn_detection": self.combo_turn.get(),
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
        self.combo_model.set("Qwen3.5-Omni Plus (多模态)")
        self.combo_model.bind("<<ComboboxSelected>>", self._on_model_changed)
        self.combo_model.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="音色:").pack(side=tk.LEFT)
        self.combo_voice = ttk.Combobox(row2, values=VOICES_OMNI, state="readonly", width=16)
        self.combo_voice.set(VOICES_OMNI[0])
        self.combo_voice.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Checkbutton(row2, text="自动模式", variable=self.auto_mode).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(row2, text="模式:").pack(side=tk.LEFT)
        self.combo_turn = ttk.Combobox(row2, values=list(TURN_DETECTION_MODES.keys()), state="readonly", width=20)
        self.combo_turn.set("Server VAD (自动)")
        self.combo_turn.pack(side=tk.LEFT, padx=(4, 0))

        # --- Control frame ---
        ctrl = ttk.Frame(self.root, padding=4)
        ctrl.pack(fill=tk.X, **pad)

        self.btn_connect = ttk.Button(ctrl, text="连接", command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=4)
        self.btn_disconnect = ttk.Button(ctrl, text="断开", command=self._on_manual_disconnect, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.btn_video = ttk.Button(ctrl, text="📷 视频", command=self._toggle_video)
        self.btn_video.pack(side=tk.LEFT, padx=4)
        self.btn_send_audio = ttk.Button(ctrl, text="🎵 发送音频", command=self._on_send_audio_file)
        self.btn_send_audio.pack(side=tk.LEFT, padx=4)
        self.btn_send_image = ttk.Button(ctrl, text="🖼️ 图片", command=self._on_send_image)
        self.btn_send_image.pack(side=tk.LEFT, padx=4)
        self.btn_send_video = ttk.Button(ctrl, text="🎬 视频", command=self._on_send_video)
        self.btn_send_video.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(ctrl, textvariable=self.status_var, foreground="gray").pack(side=tk.LEFT, padx=16)

        self.energy_var = tk.StringVar(value="")
        ttk.Label(ctrl, textvariable=self.energy_var, foreground="#888888").pack(side=tk.RIGHT, padx=8)

        # --- Video frame (hidden by default) ---
        self.video_frame = ttk.LabelFrame(self.root, text="视频画面", padding=2)
        self.video_label = tk.Label(self.video_frame, bg="black", width=320, height=240, text="摄像头未开启")
        self.video_label.pack(fill=tk.X)

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

        # --- Text input frame ---
        input_frame = ttk.Frame(self.root, padding=4)
        input_frame.pack(fill=tk.X, **pad)

        self.entry_text = ttk.Entry(input_frame, font=("Microsoft YaHei", 10))
        self.entry_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.entry_text.bind("<Return>", lambda e: self._on_send_text())

        self.btn_send_text = ttk.Button(input_frame, text="发送", command=self._on_send_text)
        self.btn_send_text.pack(side=tk.RIGHT)

        # --- Status bar ---
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.log_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.log_var, foreground="gray").pack(side=tk.LEFT, padx=8, pady=4)

        self.mic_var = tk.StringVar(value="🎤 --")
        self.mic_label = ttk.Label(bar, textvariable=self.mic_var, foreground="gray")
        self.mic_label.pack(side=tk.RIGHT, padx=8, pady=4)

        self.spk_var = tk.StringVar(value="🔊 --")
        self.spk_label = ttk.Label(bar, textvariable=self.spk_var, foreground="gray")
        self.spk_label.pack(side=tk.RIGHT, padx=8, pady=4)

    # ── Chat display ──

    def _append_chat(self, role: str, text: str):
        self.text_chat.configure(state=tk.NORMAL)
        if role == "user":
            self.text_chat.insert(tk.END, "💬 你说: ", "user")
        elif role == "user_voice":
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

    def _set_mic(self, text: str, color: str = "gray"):
        self.root.after(0, lambda: (self.mic_var.set(text), self.mic_label.configure(foreground=color)))

    def _set_spk(self, text: str, color: str = "gray"):
        self.root.after(0, lambda: (self.spk_var.set(text), self.spk_label.configure(foreground=color)))

    # ── Text input ──

    def _on_send_text(self):
        text = self.entry_text.get().strip()
        if not text:
            return
        self.entry_text.delete(0, tk.END)
        self._append_chat("user", text)
        if self.client and self.connected and self.loop:
            asyncio.run_coroutine_threadsafe(self.client.send_text(text), self.loop)
            self._last_speech_time = time.time()
        else:
            self._append_chat("system", "未连接，无法发送文字")

    # ── Audio file send ──

    def _on_send_audio_file(self):
        filepath = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                ("WAV 文件", "*.wav"),
                ("PCM 文件", "*.pcm"),
                ("所有文件", "*.*"),
            ],
        )
        if not filepath:
            return
        if not self.client or not self.connected or not self.loop:
            self._append_chat("system", "未连接，无法发送音频")
            return
        self._append_chat("system", f"正在发送音频: {os.path.basename(filepath)}")
        threading.Thread(target=self._send_audio_file_thread, args=(filepath,), daemon=True).start()

    def _send_audio_file_thread(self, filepath: str):
        try:
            import wave
            if filepath.lower().endswith(".wav"):
                with wave.open(filepath, "rb") as wf:
                    sample_rate = wf.getframerate()
                    channels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    pcm_data = wf.readframes(wf.getnframes())
            else:
                with open(filepath, "rb") as f:
                    pcm_data = f.read()
                sample_rate = 16000

            self._append_chat("system",
                f"音频: {len(pcm_data)} 字节, {sample_rate}Hz, "
                f"{len(pcm_data) / (sample_rate * 2):.1f}秒")
            asyncio.run_coroutine_threadsafe(
                self.client.send_audio_file(pcm_data), self.loop
            )
            self._last_speech_time = time.time()
        except Exception as e:
            self.root.after(0, lambda: self._append_chat("system", f"发送音频失败: {e}"))

    # ── Image input ──

    def _on_send_image(self):
        filepath = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.gif *.bmp *.webp"), ("所有", "*.*")],
        )
        if not filepath:
            return
        if not self.client or not self.connected or not self.loop:
            self._append_chat("system", "未连接，无法发送图片")
            return
        self._append_chat("user", f"[图片: {os.path.basename(filepath)}]")
        asyncio.run_coroutine_threadsafe(self.client.send_image(filepath), self.loop)
        self._last_speech_time = time.time()

    # ── Video file input ──

    def _on_send_video(self):
        filepath = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv"), ("所有", "*.*")],
        )
        if not filepath:
            return
        if not self.client or not self.connected or not self.loop:
            self._append_chat("system", "未连接，无法发送视频")
            return
        self._append_chat("user", f"[视频: {os.path.basename(filepath)}]")
        asyncio.run_coroutine_threadsafe(self.client.send_video(filepath), self.loop)
        self._last_speech_time = time.time()

    # ── Video ──

    def _toggle_video(self):
        if self._video_showing:
            self._stop_video()
        else:
            self._start_video()

    def _start_video(self):
        try:
            from video_capture import VideoCapture
        except ImportError:
            self._append_chat("system", "缺少 opencv-python 或 Pillow，请运行: pip install opencv-python Pillow")
            return

        if self.video_capture is None:
            self.video_capture = VideoCapture(self.video_frame, width=320, height=240)

        if self.video_capture.start():
            self._video_showing = True
            self.video_frame.pack(fill=tk.X, padx=8, pady=4, before=self.root.nametowidget(self.video_frame.master).winfo_children()[3])
            self.btn_video.configure(text="📷 关闭视频")
            self._append_chat("system", "摄像头已开启")
            self._update_video_frame()
        else:
            self._append_chat("system", "无法打开摄像头")

    def _stop_video(self):
        self._video_showing = False
        if self._video_after_id:
            self.root.after_cancel(self._video_after_id)
            self._video_after_id = None
        if self.video_capture:
            self.video_capture.stop()
        self.video_frame.pack_forget()
        self.btn_video.configure(text="📷 视频")
        self._append_chat("system", "摄像头已关闭")

    def _update_video_frame(self):
        if not self._video_showing or not self.video_capture:
            return
        photo = self.video_capture.get_frame_tk()
        if photo:
            self.video_label.configure(image=photo, text="")
            self.video_label._image = photo
        self._video_after_id = self.root.after(66, self._update_video_frame)  # ~15fps

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
                on_speech_state=self._on_speech_state,
            )
            self.audio.start_speaker()
            self._set_spk("🔊 已启动", "green")

        turn_mode_name = self.combo_turn.get()
        turn_mode = TURN_DETECTION_MODES.get(turn_mode_name, "server_vad")

        self.client = QwenRealtimeClient(
            api_key=api_key,
            workspace_id=ws_id,
            model=model_id,
            voice=voice,
            turn_detection=turn_mode,
            on_audio_output=self._on_ai_audio,
            on_user_text=self._on_user_text,
            on_ai_text=self._on_ai_text,
            on_status=self._on_status,
            on_error=self._on_error,
            on_speech_start=self._on_speech_start,
            on_speech_end=self._on_speech_end,
            on_response_start=self._on_response_start,
            on_response_end=self._on_response_end,
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
        self._mic_always_on = False
        self.root.after(0, self._update_buttons_connected)
        if self.audio:
            self.audio.start_microphone()
            self._set_mic("🎤 监听中", "green")
        self._append_chat("system", "连接成功！支持语音、文字输入，可发送音频文件，可开启视频")
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
        self._set_mic("🎤 已停止", "gray")
        self._set_spk("🔊 已停止", "gray")
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
        self._set_mic("🎤 --", "gray")
        self._set_spk("🔊 --", "gray")

    # ── Auto mode ──

    def _on_speech_state(self, is_speech: bool):
        """Called by AudioHandler when voice activity state changes."""
        # Ignore all VAD events during AI response or cooldown (prevent echo false triggers)
        ai_active = (self.client and
                     (self.client.is_responding or self.client._cooldown))
        if is_speech:
            self._user_speaking = True
            self._last_speech_time = time.time()
            self._reconnect_pending = False
            if self._silence_check_id:
                self.root.after_cancel(self._silence_check_id)
                self._silence_check_id = None
            if not ai_active:
                self._set_mic("🎤 说话中...", "#0078D4")
                self.root.after(0, lambda: self.energy_var.set("🎤 说话中..."))
        else:
            self._user_speaking = False
            if not ai_active:
                self._set_mic("🎤 监听中", "green")
                self.root.after(0, lambda: self.energy_var.set(""))
            # Manual mode: start silence timer only if not in cooldown
            if (self.client and self.client.is_manual
                    and self.connected and not self.client._cooldown):
                if self._silence_check_id:
                    self.root.after_cancel(self._silence_check_id)
                self._silence_check_id = self.root.after(
                    MANUAL_SILENCE_MS, self._on_manual_silence
                )

    def _on_manual_silence(self):
        """Manual mode: silence duration passed, commit and respond."""
        self._silence_check_id = None
        if self._user_speaking or not self.connected or not self.client:
            return
        if self.client._cooldown or self.client.is_responding:
            return
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.client.commit_and_respond(), self.loop
            )

    def _on_speech_start(self):
        """Called by client on server_vad speech_started event."""
        self._last_speech_time = time.time()
        self._voice_active = True
        self._reconnect_pending = False

    def _on_speech_end(self):
        """Called by client on server_vad speech_stopped event."""
        self._last_speech_time = time.time()
        self._voice_active = False

    def _on_response_start(self):
        self._set_spk("🔊 播放中", "#0078D4")

    def _on_response_end(self):
        self._set_spk("🔊 已启动", "green")

    def _on_voice_energy(self, rms: float):
        if not self.auto_mode.get():
            return
        if not self.connected and self._mic_always_on:
            bar_len = 20
            filled = min(int(rms / 200 * bar_len), bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            self.root.after(0, lambda: self.energy_var.set(f"🎧 监听中 {bar}"))

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
            self._start_mic_monitor()
            return
        if remaining <= 15:
            self._set_status(f"无语音 {int(remaining)}s 后断开")
        self._auto_check_id = self.root.after(2000, self._check_auto_disconnect)

    def _start_mic_monitor(self):
        if not self.auto_mode.get():
            return
        self._mic_always_on = True
        if self.audio is None:
            self.audio = AudioHandler(
                on_audio_chunk=self._on_mic_chunk,
                on_voice_detected=self._on_voice_energy,
                on_speech_state=self._on_speech_state,
            )
            self.audio.start_speaker()
            self._set_spk("🔊 已启动", "green")
        try:
            self.audio.start_microphone()
            self._set_mic("🎤 监听中", "green")
        except Exception:
            pass
        self._set_status("自动监听中...")

    # ── Callbacks ──

    def _on_mic_chunk(self, data: bytes):
        if self.client and self.connected and self.loop:
            # Block audio during cooldown to prevent server from detecting echo as user speech
            if self.client._cooldown:
                return
            asyncio.run_coroutine_threadsafe(self.client.send_audio(data), self.loop)

    def _on_ai_audio(self, data: bytes):
        if self.audio:
            self.audio.play_audio(data)

    def _on_user_text(self, text: str):
        self.root.after(0, lambda: self._append_chat("user_voice", text))

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
            if self._video_showing:
                self._stop_video()
        except Exception:
            pass
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
