"""
AIChat_Doubao_Audio.py
豆包全链路语音聊天：
  麦克风录音（PyAudio VAD）
    → 豆包 ASR 语音识别
    → 豆包大模型对话
    → 豆包 TTS 真人语音合成
    → 扬声器播放
"""

import sys
import io
import os
import wave
import struct
import math
import time
import datetime
import threading
import requests
import pyaudio

# ── Windows 控制台 UTF-8 ──────────────────────────────────────────────
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

# ════════════════════════════════════════════════════════════════════════
# 配置（按需修改）
# ════════════════════════════════════════════════════════════════════════

ARK_API_KEY  = "ark-ce45b71c-48f6-4b17-bf2d-95b41405ffff-7b27f"  # 火山方舟 API Key
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# 大模型
CHAT_MODEL = "doubao-seed-2-0-pro-260215"

# ASR（语音识别）- 火山方舟 OpenAI 兼容接口
ASR_MODEL  = "doubao-asr"

# TTS（语音合成）- 火山方舟 OpenAI 兼容接口
TTS_MODEL  = "doubao-tts-hd"
# 音色参考：https://www.volcengine.com/docs/6561/97465
# 女声：BV001_streaming  男声：BV700_streaming  客服：BV002_streaming
TTS_VOICE  = "BV001_streaming"
TTS_RATE   = 24000   # 豆包 TTS PCM 采样率（Hz）

# ── 录音参数 ──────────────────────────────────────────────────────────
MIC_RATE    = 16000
MIC_CHANNEL = 1
MIC_FORMAT  = pyaudio.paInt16
MIC_CHUNK   = 1024

# ── VAD 参数 ──────────────────────────────────────────────────────────
VAD_THRESHOLD  = 600   # RMS 音量阈值，安静环境可调低（如400），嘈杂环境调高
VAD_CONFIRM    = 3     # 连续 N 帧超阈值才确认为说话
PRE_BUFFER_SEC = 0.5   # 说话触发前保留的缓冲秒数（避免丢失开头）
PAUSE_SEC      = 2.0   # 停顿超过此秒数结束录音

# ════════════════════════════════════════════════════════════════════════
# 对话历史
# ════════════════════════════════════════════════════════════════════════

_chat_history: list = []


def _system_prompt() -> str:
    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    return (f"你是一个智能语音助手，当前时间是 {now}，"
            "请用用户相同的语言回答问题，回答要简短精炼，不超过3句话。")


# ════════════════════════════════════════════════════════════════════════
# 1. 麦克风录音（含 VAD，自动检测说话开始与结束）
# ════════════════════════════════════════════════════════════════════════

def _select_mic_index() -> int | None:
    """选择麦克风设备索引，优先使用系统默认；多设备时交互选择。"""
    pa = pyaudio.PyAudio()
    inputs = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            name = info["name"]
            # Windows 乱码修复
            if sys.platform == "win32":
                try:
                    name = name.encode("latin-1").decode("gbk")
                except Exception:
                    try:
                        name = name.encode("utf-8").decode("gbk")
                    except Exception:
                        pass
            inputs.append((i, name))
    pa.terminate()
    if not inputs:
        return None
    if len(inputs) == 1:
        print(f"[麦克风] 使用: {inputs[0][1]}")
        return inputs[0][0]
    # 多设备：尝试系统默认
    pa2 = pyaudio.PyAudio()
    try:
        default = pa2.get_default_input_device_info()["index"]
        pa2.terminate()
        name = next((n for i, n in inputs if i == default), "")
        print(f"[麦克风] 使用默认: {name}")
        return default
    except OSError:
        pa2.terminate()
    print("\n检测到多个麦克风，请选择：")
    for idx, (dev_i, name) in enumerate(inputs):
        print(f"  {idx + 1}. [{dev_i}] {name}")
    while True:
        c = input(f"输入编号（1-{len(inputs)}）: ").strip()
        if c.isdigit() and 1 <= int(c) <= len(inputs):
            sel = inputs[int(c) - 1]
            print(f"[麦克风] 已选择: {sel[1]}")
            return sel[0]


MIC_DEVICE_INDEX = _select_mic_index()


def record_audio() -> bytes | None:
    """
    从麦克风录音，含 VAD：
      - 等待说话（音量超阈值连续 VAD_CONFIRM 帧）
      - 自动保留说话前 PRE_BUFFER_SEC 秒的帧（避免丢头）
      - 停顿超过 PAUSE_SEC 秒后自动结束
    返回 WAV 格式字节，失败返回 None。
    """
    if MIC_DEVICE_INDEX is None:
        print("[录音] 无可用麦克风设备")
        return None

    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=MIC_FORMAT, channels=MIC_CHANNEL, rate=MIC_RATE,
        input=True, input_device_index=MIC_DEVICE_INDEX,
        frames_per_buffer=MIC_CHUNK,
    )

    pre_max   = int(MIC_RATE * PRE_BUFFER_SEC / MIC_CHUNK)
    pause_max = int(MIC_RATE * PAUSE_SEC / MIC_CHUNK)

    rolling       = []
    speech_frames = []
    speaking      = False
    consecutive   = 0
    silent        = 0

    print("?? 正在聆听，请说话...", flush=True)

    try:
        while True:
            data   = stream.read(MIC_CHUNK, exception_on_overflow=False)
            count  = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0

            if not speaking:
                rolling.append(data)
                if len(rolling) > pre_max:
                    rolling.pop(0)
                if rms > VAD_THRESHOLD:
                    consecutive += 1
                    if consecutive >= VAD_CONFIRM:
                        speaking = True
                        speech_frames = list(rolling)
                        print("?? 录音中（停顿2秒后结束）...", flush=True)
                else:
                    consecutive = 0
            else:
                speech_frames.append(data)
                if rms < VAD_THRESHOLD * 0.6:
                    silent += 1
                    if silent >= pause_max:
                        break
                else:
                    silent = 0
                if len(speech_frames) > int(MIC_RATE * 60 / MIC_CHUNK):
                    break
    except KeyboardInterrupt:
        raise
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    if not speech_frames:
        return None

    duration = len(speech_frames) * MIC_CHUNK / MIC_RATE
    print(f"? 录音结束（{duration:.1f}s）", flush=True)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(MIC_CHANNEL)
        wf.setsampwidth(2)          # paInt16 = 2 bytes
        wf.setframerate(MIC_RATE)
        wf.writeframes(b"".join(speech_frames))
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════════════
# 2. 豆包 ASR 语音识别
# ════════════════════════════════════════════════════════════════════════

def asr(wav_bytes: bytes) -> str:
    """
    调用豆包 ASR API（火山方舟 OpenAI 兼容 /audio/transcriptions）。
    返回识别到的文字，失败返回空字符串。
    """
    url     = f"{ARK_BASE_URL}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {ARK_API_KEY}"}
    files   = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data    = {"model": ASR_MODEL}
    try:
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("text", "").strip()
    except Exception as e:
        print(f"[ASR] 识别失败: {e}")
        return ""


# ════════════════════════════════════════════════════════════════════════
# 3. 豆包大模型对话
# ════════════════════════════════════════════════════════════════════════

def chat_llm(user_text: str) -> str:
    """调用豆包大模型，返回回复文字。"""
    global _chat_history
    _chat_history.append({"role": "user", "content": user_text})
    url     = f"{ARK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": _system_prompt()}] + _chat_history,
        "temperature": 0.7,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"]
        _chat_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"对话失败：{e}"


# ════════════════════════════════════════════════════════════════════════
# 4. 豆包 TTS 语音合成
# ════════════════════════════════════════════════════════════════════════

def tts(text: str) -> bytes | None:
    """
    调用豆包 TTS API（火山方舟 OpenAI 兼容 /audio/speech）。
    返回 PCM 音频字节（16-bit, TTS_RATE Hz, mono），失败返回 None。
    """
    url     = f"{ARK_BASE_URL}/audio/speech"
    headers = {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TTS_MODEL,
        "input": text,
        "voice": TTS_VOICE,
        "response_format": "pcm",   # 直接 PCM，无需解码，可用 PyAudio 直接播放
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"[TTS] 合成失败: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════
# 5. PCM 音频播放（支持打断）
# ════════════════════════════════════════════════════════════════════════

class _Player:
    """可打断的 PCM 播放器。"""

    def __init__(self, pcm: bytes, rate: int):
        self._pcm      = pcm
        self._rate     = rate
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        pa     = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1,
                         rate=self._rate, output=True)
        chunk  = 4096
        pos    = 0
        try:
            while pos < len(self._pcm) and not self._stop.is_set():
                stream.write(self._pcm[pos: pos + chunk])
                pos += chunk
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def is_playing(self) -> bool:
        return self._thread.is_alive()

    def wait(self):
        self._thread.join()


def play_pcm_interruptible(pcm: bytes, rate: int = TTS_RATE) -> bool:
    """
    播放 PCM 音频，同时监听麦克风。
    用户开口说话时立即停止播放。
    返回 True 表示被打断，False 表示正常播完。
    """
    player    = _Player(pcm, rate)
    stopped   = threading.Event()
    captured  = [b""]   # 打断时已录到的帧

    def _vad():
        if MIC_DEVICE_INDEX is None:
            return
        try:
            pa     = pyaudio.PyAudio()
            stream = pa.open(
                format=MIC_FORMAT, channels=MIC_CHANNEL, rate=MIC_RATE,
                input=True, input_device_index=MIC_DEVICE_INDEX,
                frames_per_buffer=MIC_CHUNK,
            )
            time.sleep(0.3)   # 等 TTS 播放稳定，避免扬声器反馈误触发

            pre_max    = int(MIC_RATE * PRE_BUFFER_SEC / MIC_CHUNK)
            pause_max  = int(MIC_RATE * PAUSE_SEC / MIC_CHUNK)
            rolling    = []
            consecutive = 0

            # 阶段1：等待说话
            while player.is_playing() and not stopped.is_set():
                data   = stream.read(MIC_CHUNK, exception_on_overflow=False)
                rolling.append(data)
                if len(rolling) > pre_max:
                    rolling.pop(0)
                count  = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0
                if rms > VAD_THRESHOLD:
                    consecutive += 1
                    if consecutive >= VAD_CONFIRM:
                        stopped.set()
                        break
                else:
                    consecutive = 0

            if not stopped.is_set():
                stream.stop_stream(); stream.close(); pa.terminate()
                return

            # 停止播放
            player.stop()
            print("\n[打断] 正在聆听...", flush=True)

            # 阶段2：在同一流上继续录音直到静音（保留pre-buffer，不丢开头）
            frames  = list(rolling)
            silent  = 0
            max_f   = int(MIC_RATE * 60 / MIC_CHUNK)
            while len(frames) < max_f:
                data = stream.read(MIC_CHUNK, exception_on_overflow=False)
                frames.append(data)
                count  = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0
                if rms < VAD_THRESHOLD * 0.6:
                    silent += 1
                    if silent >= pause_max:
                        break
                else:
                    silent = 0

            stream.stop_stream(); stream.close(); pa.terminate()

            # 打包 WAV
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(MIC_CHANNEL)
                wf.setsampwidth(2)
                wf.setframerate(MIC_RATE)
                wf.writeframes(b"".join(frames))
            captured[0] = buf.getvalue()

        except Exception as e:
            print(f"[VAD] 异常: {e}")

    vad_thread = threading.Thread(target=_vad, daemon=True)
    player.start()
    vad_thread.start()
    player.wait()
    stopped.set()
    vad_thread.join(timeout=30)

    return captured[0] != b"", captured[0]


# ════════════════════════════════════════════════════════════════════════
# 主循环
# ════════════════════════════════════════════════════════════════════════

_EXIT_WORDS = {"退出", "再见", "结束对话", "拜拜", "bye", "exit", "quit"}


def main():
    print("=" * 55)
    print("     豆包 语音聊天助手")
    print("     ASR 语音识别 + 大模型对话 + TTS 语音播报")
    print("=" * 55)
    print("说话自动开始，停顿 2 秒自动结束")
    print("说「退出 / 再见」或按 Ctrl+C 结束程序")
    print("=" * 55)

    interrupted_wav: bytes | None = None   # 打断播放时录到的音频

    while True:
        try:
            # ── 获取用户语音 ─────────────────────────────────────────
            if interrupted_wav:
                # 上一轮播放被打断，直接用打断时录到的音频，不重新录音
                wav = interrupted_wav
                interrupted_wav = None
            else:
                wav = record_audio()
                if not wav:
                    print("[录音] 未检测到声音，请重试。\n")
                    continue

            # ── ASR 识别 ─────────────────────────────────────────────
            print("?? 识别中...", flush=True)
            user_text = asr(wav)
            if not user_text:
                print("[ASR] 未识别到内容，请重试。\n")
                continue
            print(f"\n你：{user_text}")

            # 退出命令检测
            if any(w in user_text for w in _EXIT_WORDS):
                farewell = "再见！期待下次和你聊天。"
                print(f"AI：{farewell}\n")
                pcm = tts(farewell)
                if pcm:
                    _Player(pcm, TTS_RATE).start()
                    time.sleep(len(pcm) / (TTS_RATE * 2) + 0.5)
                break

            # ── 大模型对话 ────────────────────────────────────────────
            print("?? 思考中...", flush=True)
            reply = chat_llm(user_text)
            print(f"AI：{reply}\n")

            # ── TTS 合成 ──────────────────────────────────────────────
            print("?? 播放中（说话可打断）...", flush=True)
            pcm = tts(reply)
            if not pcm:
                print("[TTS] 语音合成失败，仅显示文字。\n")
                continue

            # ── 播放（可被打断）──────────────────────────────────────
            was_interrupted, interrupt_wav = play_pcm_interruptible(pcm)
            if was_interrupted and interrupt_wav:
                interrupted_wav = interrupt_wav   # 下一轮直接用，跳过重新录音

        except KeyboardInterrupt:
            print("\n\n再见！")
            break
        except Exception as e:
            print(f"[错误] {e}\n")


if __name__ == "__main__":
    main()
