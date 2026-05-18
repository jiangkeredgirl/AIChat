# -*- coding: utf-8 -*-
"""
AIChat_Doubao_Audio_Stream.py
豆包 AI 语音 + 文字聊天助手

  文字模式：键盘输入 → 豆包 LLM 流式回复 → 终端打印
  语音模式：
    麦克风录音（VAD）→ 豆包流式ASR识别 → faster-whisper 兜底
    → 豆包 LLM 流式回复
    → 豆包 TTS 真人语音合成（HMAC 签名）/ pyttsx3 兜底
    → 扬声器播放（可随时打断）

快捷操作：
  说"退出/再见"或输入"退出"或 Ctrl+C 退出
"""

import sys, io, os, json, time, datetime, struct, math
import threading, queue, uuid, hmac, hashlib, base64
import wave, subprocess, tempfile
import requests, pyaudio
import numpy as _np

# ── Windows UTF-8 ──────────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

# ════════════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════════════

# ── 豆包 LLM（火山方舟） ───────────────────────────────────────────────
ARK_API_KEY  = "ark-ce45b71c-48f6-4b17-bf2d-95b41405ffff-7b27f"
ARK_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
CHAT_MODEL   = "doubao-seed-2-0-pro-260215"

# ── 豆包 TTS（火山引擎语音合成） ──────────────────────────────────────
APP_ID       = "9928059183"
ACCESS_TOKEN = "ni5KWLvTq2efj7JfJHfXO9iUv8hcOHVu"
SECRET_KEY   = "AUgB1kFRczyKxmbVnP0C6bjn9U9Mzix-"
TTS_URL      = "https://openspeech.bytedance.com/api/v1/tts"
TTS_CLUSTER  = "volcano_tts"          # SeedTTS2.0 实例集群
TTS_VOICE    = "温柔桃子"               # 使用“温柔桃子”音色
TTS_RATE     = 24000                  # PCM 采样率

# ── faster-whisper ASR ─────────────────────────────────────────────────
WHISPER_MODEL_SIZE = "small"          # tiny / base / small / medium

# ── Moonshine ASR（moonshine_voice）─────────────────────────────────────
MOONSHINE_MODEL_SIZE = "base"         # tiny / base
MOONSHINE_LANGUAGE   = "zh"

# ── ASR 识别库选择：auto / doubao / whisper / moonshine ────────────────
ASR_BACKEND = "auto"

# ── 音频录音参数 ───────────────────────────────────────────────────────
MIC_RATE        = 16000
MIC_CHUNK       = 1024
VAD_THRESHOLD   = 400   # RMS 基准阈值（会被环境校准覆盖）
VAD_CONFIRM     = 3     # 连续 N 帧超阈值才确认说话
PRE_BUF_SEC     = 0.8   # 说话前保留缓冲（秒）
PAUSE_SEC       = 2.0   # 停顿超过此秒数结束录音
LISTEN_TIMEOUT  = 30.0  # 等待说话的最长秒数，超时返回 None

EXIT_WORDS = {"退出", "再见", "结束", "拜拜", "quit", "exit", "bye"}

# ════════════════════════════════════════════════════════════════════════
# 初始化：faster-whisper 模型 + pyttsx3 兜底
# ════════════════════════════════════════════════════════════════════════

print("[初始化] 加载 faster-whisper 模型...", flush=True)
try:
    from faster_whisper import WhisperModel as _WhisperModel
    _whisper = _WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    print(f"[初始化] faster-whisper {WHISPER_MODEL_SIZE} 加载完成")
except Exception as e:
    _whisper = None
    print(f"[初始化] faster-whisper 加载失败: {e}")

print("[初始化] 加载 Moonshine 模型...", flush=True)
try:
    from moonshine_voice import get_model_for_language as _ms_get_model_for_language
    from moonshine_voice import ModelArch as _MSModelArch
    from moonshine_voice.transcriber import Transcriber as _MSTranscriber

    _ms_arch = _MSModelArch.BASE if MOONSHINE_MODEL_SIZE == "base" else _MSModelArch.TINY
    _moonshine_model_path, _moonshine_model_arch = _ms_get_model_for_language(
        MOONSHINE_LANGUAGE, _ms_arch
    )
    _moonshine = _MSTranscriber(
        model_path=_moonshine_model_path,
        model_arch=_moonshine_model_arch,
    )
    print(
        f"[初始化] Moonshine {MOONSHINE_MODEL_SIZE}-{MOONSHINE_LANGUAGE} 加载完成",
        flush=True,
    )
except Exception as e:
    _moonshine = None
    print(f"[初始化] Moonshine 加载失败: {e}")
    print("[提示] 可运行: pip install moonshine-voice", flush=True)

_VALID_ASR_BACKENDS = {"auto", "doubao", "whisper", "moonshine"}
if ASR_BACKEND not in _VALID_ASR_BACKENDS:
    print(f"[配置] ASR_BACKEND={ASR_BACKEND} 无效，已回退到 auto")
    ASR_BACKEND = "auto"

_SELECTED_ASR_BACKEND = ASR_BACKEND


def _choose_asr_backend() -> str:
    global _SELECTED_ASR_BACKEND
    print("\nASR识别库选择：")
    print("  1. 自动（豆包优先，失败后 moonshine，再 whisper）")
    print("  2. 豆包流式ASR")
    print("  3. Moonshine")
    print("  4. faster-whisper")
    c = input("选择ASR (1/2/3/4，回车按配置): ").strip()
    mapping = {"1": "auto", "2": "doubao", "3": "moonshine", "4": "whisper"}
    if c in mapping:
        _SELECTED_ASR_BACKEND = mapping[c]
    print(f"[ASR] 当前识别库: {_SELECTED_ASR_BACKEND}")
    return _SELECTED_ASR_BACKEND


def _asr_backend_available(name: str) -> bool:
    if name == "doubao":
        return WS_AVAILABLE
    if name == "moonshine":
        return _moonshine is not None
    if name == "whisper":
        return _whisper is not None
    return True


def _asr_fallback_order(name: str) -> list[str]:
    if name == "doubao":
        return ["doubao", "moonshine", "whisper"]
    if name == "moonshine":
        return ["moonshine", "doubao", "whisper"]
    if name == "whisper":
        return ["whisper", "doubao", "moonshine"]
    return ["doubao", "moonshine", "whisper"]


def _run_asr_backend(name: str, wav_bytes: bytes) -> str:
    if name == "doubao":
        return asr_doubao_stream(wav_bytes)
    if name == "moonshine":
        return asr_moonshine(wav_bytes)
    if name == "whisper":
        return asr_whisper(wav_bytes)
    return ""

try:
    import pyttsx3 as _pyttsx3
    _PYTTSX3_OK = True
except ImportError:
    _PYTTSX3_OK = False

# ════════════════════════════════════════════════════════════════════════
# 麦克风选择
# ════════════════════════════════════════════════════════════════════════

def _fix_mic_name(raw: str) -> str:
    """尝试将 PyAudio 在 Windows 上返回的乱码设备名修复为正确 Unicode。
    PyAudio 有时把 GBK 字节当 latin-1 解码，这里尝试还原。
    """
    if sys.platform != "win32":
        return raw
    # 如果已包含合法中文字符则无需修复
    if any("\u4e00" <= c <= "\u9fff" for c in raw):
        return raw
    try:
        return raw.encode("latin-1").decode("gbk")
    except Exception:
        pass
    try:
        return raw.encode("utf-8").decode("gbk")
    except Exception:
        pass
    return raw


def _pick_mic() -> "int | None":
    pa = pyaudio.PyAudio()
    inputs = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            name = _fix_mic_name(info.get("name", ""))
            inputs.append((i, name))
    pa.terminate()
    if not inputs:
        print("[麦克风] 未找到任何输入设备")
        return None
    if len(inputs) == 1:
        print(f"[麦克风] 使用: {inputs[0][1]}"); return inputs[0][0]
    # 尝试获取系统默认输入设备
    pa2 = pyaudio.PyAudio()
    default_idx = None
    try:
        default_idx = pa2.get_default_input_device_info()["index"]
    except OSError:
        pass
    finally:
        pa2.terminate()
    if default_idx is not None and any(i == default_idx for i, _ in inputs):
        name = next(n for i, n in inputs if i == default_idx)
        print(f"[麦克风] 默认: [{default_idx}] {name}")
        return default_idx
    # 让用户手动选择
    print("\n检测到多个麦克风，请选择：")
    for k, (di, n) in enumerate(inputs):
        print(f"  {k+1}. [{di}] {n}")
    while True:
        c = input(f"输入编号(1-{len(inputs)}): ").strip()
        if c.isdigit() and 1 <= int(c) <= len(inputs):
            sel = inputs[int(c) - 1]
            print(f"[麦克风] 已选: {sel[1]}")
            return sel[0]


MIC_IDX = _pick_mic()

# ════════════════════════════════════════════════════════════════════════
# 系统提示（含实时时间）
# ════════════════════════════════════════════════════════════════════════

def _sysprompt() -> str:
    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    return (f"你是一个智能语音助手，当前时间是 {now}。"
            "请用用户相同的语言回答，回答简短精炼，不超过3句话。"
            "被询问时间时，报告当前具体时间。")

# ════════════════════════════════════════════════════════════════════════
# 豆包 LLM（流式）
# ════════════════════════════════════════════════════════════════════════

_history: list = []


def llm_stream(user: str) -> str:
    """流式调用豆包 LLM，打印到终端，返回完整回复。"""
    _history.append({"role": "user", "content": user})
    headers = {"Authorization": f"Bearer {ARK_API_KEY}",
               "Content-Type": "application/json"}
    payload = {"model": CHAT_MODEL,
               "messages": [{"role": "system", "content": _sysprompt()}] + _history,
               "temperature": 0.7, "stream": True}
    print("\nAI: ", end="", flush=True)
    full = ""
    try:
        with requests.post(ARK_CHAT_URL, headers=headers, json=payload,
                           stream=True, timeout=30) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[6:]
                if text.strip() == "[DONE]":
                    break
                try:
                    c = json.loads(text)["choices"][0].get("delta", {}).get("content", "")
                    if c:
                        print(c, end="", flush=True)
                        full += c
                except Exception:
                    pass
    except Exception as e:
        print(f"\n[LLM错误] {e}")
    print()
    if full:
        _history.append({"role": "assistant", "content": full})
    return full

# ════════════════════════════════════════════════════════════════════════
# 豆包 TTS（HMAC-SHA256 签名认证）+ pyttsx3 兜底
# ════════════════════════════════════════════════════════════════════════

def _tts_sign(app_id: str, token: str, secret: str) -> dict:
    """生成豆包 TTS API 所需的 HMAC-SHA256 Authorization 头。"""
    ts = str(int(time.time()))
    to_sign = f"{app_id}{ts}"
    sig = hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer;{app_id};{token};{sig}",  # no spaces – server splits on ';'
        "X-Timestamp":   ts,
        "Content-Type":  "application/json",
    }


def tts_doubao(text: str) -> bytes | None:
    """调用豆包 TTS API，返回 PCM 字节（24000Hz, mono, int16）。"""
    body = {
        "app":  {"appid": APP_ID, "token": ACCESS_TOKEN, "cluster": TTS_CLUSTER},
        "user": {"uid": "aichat_user"},
        "audio": {
            "voice_type": TTS_VOICE,
            "encoding":   "pcm",
            "rate":       TTS_RATE,
            "channel":    1,
            "bits":       16,
            "speed_ratio": 1.0,
        },
        "request": {
            "reqid":     str(uuid.uuid4()),
            "text":      text,
            "text_type": "plain",
            "operation": "query",
        },
    }
    try:
        r = requests.post(TTS_URL,
                          headers=_tts_sign(APP_ID, ACCESS_TOKEN, SECRET_KEY),
                          json=body, timeout=20)
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == 3000 and data.get("data"):
                return base64.b64decode(data["data"])
            print(f"[TTS] 豆包返回错误: {data.get('message')}")
        else:
            print(f"[TTS] HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[TTS] 请求失败: {e}")
    return None


def _tts_pyttsx3(text: str):
    """pyttsx3 语音合成（兜底，子进程方式避免单例问题）。"""
    if not _PYTTSX3_OK:
        return
    script = (
        "import pyttsx3; e=pyttsx3.init();"
        "e.setProperty('rate',180); e.setProperty('volume',1.0);"
        f"e.say({repr(text)}); e.runAndWait()"
    )
    try:
        subprocess.run([sys.executable, "-c", script], timeout=30, check=False)
    except Exception as e:
        print(f"[TTS兜底] {e}")


# ════════════════════════════════════════════════════════════════════════
# 可打断 PCM 播放器
# ════════════════════════════════════════════════════════════════════════

class _Player:
    """在后台线程播放 PCM，支持随时 interrupt() 打断。"""

    def __init__(self, pcm: bytes, rate: int):
        self._pcm   = pcm
        self._rate  = rate
        self._stop  = threading.Event()
        self._done  = threading.Event()
        self._t     = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        pa     = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1,
                         rate=self._rate, output=True)
        chunk = 4096
        pos   = 0
        try:
            while pos < len(self._pcm) and not self._stop.is_set():
                stream.write(self._pcm[pos:pos + chunk])
                pos += chunk
        finally:
            stream.stop_stream(); stream.close(); pa.terminate()
            self._done.set()

    def start(self): self._t.start()
    def interrupt(self): self._stop.set()
    def wait(self, timeout=None): self._done.wait(timeout)
    def is_playing(self): return self._t.is_alive() and not self._done.is_set()


def play_interruptible(pcm: bytes, rate: int) -> bytes | None:
    """
    播放 PCM，同时用 VAD 监听麦克风。
    用户开口说话 → 停止播放 → 继续录音直到静音。
    返回打断时录到的 WAV bytes（含触发前缓冲），未打断返回 None。
    """
    player   = _Player(pcm, rate)
    stopped  = threading.Event()
    wav_box  = [None]   # 打断时录到的音频

    def _vad():
        if MIC_IDX is None:
            return
        pre_max  = int(MIC_RATE * PRE_BUF_SEC / MIC_CHUNK)
        pause_max = int(MIC_RATE * PAUSE_SEC / MIC_CHUNK)
        pa       = pyaudio.PyAudio()
        stream   = pa.open(format=pyaudio.paInt16, channels=1,
                           rate=MIC_RATE, input=True,
                           input_device_index=MIC_IDX,
                           frames_per_buffer=MIC_CHUNK)
        time.sleep(0.4)   # 等扬声器音频稳定，防回声误触
        # 打断阈值：使用校准后的阈值（若有），否则用默认值，并提高 50% 避免回声
        interrupt_thr = max(_cached_threshold * 1.2, VAD_THRESHOLD * 2) if _cached_threshold else VAD_THRESHOLD * 2
        rolling     = []
        consecutive = 0
        try:
            # 阶段1：等待说话
            while player.is_playing() and not stopped.is_set():
                data = stream.read(MIC_CHUNK, exception_on_overflow=False)
                rolling.append(data)
                if len(rolling) > pre_max:
                    rolling.pop(0)
                count  = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0
                if rms > interrupt_thr:
                    consecutive += 1
                    if consecutive >= VAD_CONFIRM:
                        stopped.set(); break
                else:
                    consecutive = 0

            if not stopped.is_set():
                return

            # 打断：停止播放
            player.interrupt()
            print("\n[打断] 正在聆听...", flush=True)

            # 阶段2：同一流继续录音（保留 pre-buffer）
            frames = list(rolling)
            silent = 0
            while len(frames) < int(MIC_RATE * 60 / MIC_CHUNK):
                data = stream.read(MIC_CHUNK, exception_on_overflow=False)
                frames.append(data)
                count  = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0
                if rms < interrupt_thr * 0.5:
                    silent += 1
                    if silent >= pause_max:
                        break
                else:
                    silent = 0

            # 打包 WAV
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(MIC_RATE)
                wf.writeframes(b"".join(frames))
            wav_box[0] = buf.getvalue()

        except Exception as e:
            print(f"[VAD] {e}")
        finally:
            stream.stop_stream(); stream.close(); pa.terminate()

    vad_t = threading.Thread(target=_vad, daemon=True)
    player.start()
    vad_t.start()
    player.wait(timeout=120)
    stopped.set()
    vad_t.join(timeout=20)
    return wav_box[0]   # None = 正常播完；bytes = 被打断时录到的语音


# ════════════════════════════════════════════════════════════════════════
# 豆包流式语音识别 ASR（Volcengine BigModel WebSocket 二进制协议）
# ════════════════════════════════════════════════════════════════════════
# 协议参考：https://www.volcengine.com/docs/6561/80816
# 帧格式：4字节头 | 4字节payload长度(大端) | payload

try:
    import websocket as _websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("[提示] 未安装 websocket-client，豆包流式ASR不可用。"
          "可运行: pip install websocket-client")

# 豆包流式ASR（V2）—— 此 APP_ID 下 V3 BigModel 的 resource_id 权限错误，改用 V2 端点
ASR_WS_URL       = "wss://openspeech.bytedance.com/api/v2/asr"
ASR_ACCESS_TOKEN = "ni5KWLvTq2efj7JfJHfXO9iUv8hcOHVu"
ASR_CLUSTER      = "volcano_asr_vc_bigmodel"

# 消息类型
_MSG_FULL_CLIENT  = 0x01
_MSG_AUDIO_ONLY   = 0x02
_MSG_FULL_SERVER  = 0x09
_FLAG_END_STREAM  = 0x02


def _build_asr_frame(msg_type: int, payload: bytes, flags: int = 0,
                     serialization: int = 0x01) -> bytes:
    """构建 Volcengine V2 ASR WebSocket 二进制帧。
    客户端帧格式: header(4) + payload_size(4大端) + payload
      byte0 = 0x11: version=1, header_size=1 — 服务端平鸪客户端 header_size，按 header(4)+size(4)+payload 解析
      服务端响应将包含额外 4字节序列号，此处 _parse_asr_frame 会自动处理
    """
    header = bytes([
        0x11,                             # version=1, header_size=1
        (msg_type << 4) | flags,
        (serialization << 4) | 0x00,
        0x00,
    ])
    return header + struct.pack(">I", len(payload)) + payload


def _parse_asr_frame(data: bytes):
    """解析服务端帧，返回 (msg_type, result_dict or None)。
    帧格式: header(4) + header_size*4字节额外头 + payload_size(4) + payload
    """
    if len(data) < 8:
        return None, None
    msg_type         = (data[1] >> 4) & 0x0F
    header_size      = data[0] & 0x0F          # 额外头字段数量（每个 4 字节）
    size_offset      = 4 + header_size * 4     # payload_size 字段的起始位置
    payload_start    = size_offset + 4
    if len(data) < payload_start:
        return msg_type, None
    payload_size = struct.unpack(">I", data[size_offset: size_offset + 4])[0]
    payload      = data[payload_start: payload_start + payload_size]
    try:
        return msg_type, json.loads(payload.decode("utf-8"))
    except Exception:
        return msg_type, None


def asr_doubao_stream(wav_bytes: bytes) -> str:
    """
    通过豆包流式ASR（BigModel WebSocket）识别 WAV 音频。
    返回识别文字；失败返回空字符串。
    """
    if not WS_AVAILABLE:
        return ""
    # 提取 PCM（去掉 WAV 头）
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
    except Exception as e:
        print(f"[ASR] WAV解析失败: {e}")
        return ""

    result_texts = []
    done_event   = threading.Event()
    req_id       = str(uuid.uuid4())

    config = {
        "app": {
            "appid":   APP_ID,
            "token":   ASR_ACCESS_TOKEN,
            "cluster": ASR_CLUSTER,         # volcano_asr_vc_bigmodel
        },
        "user": {"uid": "aichat_user"},
        "request": {
            "reqid":           req_id,
            "nbest":           1,
            "workflow":        "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuation",
            "show_language":   False,
            "show_utterances": False,
            "result_type":     "single",
            "sequence":        1,
        },
        "audio": {
            "format":  "pcm",
            "rate":    MIC_RATE,
            "bits":    16,
            "channel": 1,
            "codec":   "raw",
        },
    }

    def on_open(ws):
        def _send():
            # 首帧：携带配置 JSON
            config_bytes = json.dumps(config).encode("utf-8")
            ws.send(_build_asr_frame(_MSG_FULL_CLIENT, config_bytes, serialization=0x01),
                    opcode=0x2)
            # 分块发送 PCM
            chunk_size = MIC_CHUNK * 2
            offset = 0
            while offset < len(pcm):
                chunk   = pcm[offset: offset + chunk_size]
                offset += chunk_size
                is_last = offset >= len(pcm)
                ws.send(_build_asr_frame(_MSG_AUDIO_ONLY, chunk,
                                         flags=_FLAG_END_STREAM if is_last else 0,
                                         serialization=0x00),
                        opcode=0x2)
                time.sleep(0.01)
        threading.Thread(target=_send, daemon=True).start()

    def on_message(ws, message):
        # 服务端可能返回二进制帧或文本 JSON
        if isinstance(message, bytes):
            msg_type, result = _parse_asr_frame(message)
        else:
            try:
                result = json.loads(message)
            except Exception:
                return
        if result:
            for utt in result.get("result", {}).get("utterances", []):
                t = utt.get("text", "").strip()
                if t:
                    result_texts.append(t)
            if result.get("is_final", False):
                done_event.set()
                ws.close()

    def on_error(ws, error):
        print(f"[ASR] WebSocket错误: {error}")
        done_event.set()

    def on_close(ws, *_):
        done_event.set()

    ws_app = _websocket.WebSocketApp(
        ASR_WS_URL,
        header={"Authorization": f"Bearer;{ASR_ACCESS_TOKEN}"},  # 无空格
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    t = threading.Thread(target=lambda: ws_app.run_forever(ping_interval=20), daemon=True)
    t.start()
    done_event.wait(timeout=30)
    ws_app.close()
    return "".join(result_texts).strip()


# ════════════════════════════════════════════════════════════════════════
# faster-whisper ASR（豆包ASR失败时兜底）
# ════════════════════════════════════════════════════════════════════════

_cached_threshold: float = 0.0


def asr_moonshine(wav_bytes: bytes) -> str:
    """使用 Moonshine（moonshine_voice）识别 WAV 字节，返回文字。"""
    if _moonshine is None:
        return ""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sample_rate = wf.getframerate()
        audio_f32 = _np.frombuffer(frames, dtype=_np.int16).astype(_np.float32) / 32768.0
        transcript = _moonshine.transcribe_without_streaming(audio_f32.tolist(), sample_rate=sample_rate)
        lines = getattr(transcript, "lines", []) or []
        return "".join((ln.text or "") for ln in lines).strip()
    except Exception as e:
        print(f"[ASR] Moonshine错误: {e}")
        return ""


def asr_whisper(wav_bytes: bytes) -> str:
    """使用 faster-whisper 识别 WAV 字节，返回文字。"""
    if _whisper is None:
        return ""
    try:
        raw = io.BytesIO(wav_bytes)
        with wave.open(raw, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        audio_np = _np.frombuffer(frames, dtype=_np.int16).astype(_np.float32) / 32768.0
        segs, _  = _whisper.transcribe(audio_np, language="zh", beam_size=3,
                                        vad_filter=True,
                                        vad_parameters={"min_silence_duration_ms": 300})
        return "".join(s.text for s in segs).strip()
    except Exception as e:
        print(f"[ASR] faster-whisper错误: {e}")
        return ""


def asr_recognize(wav_bytes: bytes) -> str:
    """ASR 统一入口：按当前选择的识别库执行，并自动尝试可用兜底。"""
    backend = _SELECTED_ASR_BACKEND if _SELECTED_ASR_BACKEND in _VALID_ASR_BACKENDS else "auto"
    tried = []
    for name in _asr_fallback_order(backend):
        if not _asr_backend_available(name):
            continue
        tried.append(name)
        text = _run_asr_backend(name, wav_bytes)
        if text:
            return text
        print(f"[ASR] {name} 无结果，尝试下一个...", flush=True)

    if not tried:
        print("[ASR] 当前无可用识别库（doubao/moonshine/whisper）", flush=True)
    else:
        print(f"[ASR] 已尝试 {', '.join(tried)}，均无结果", flush=True)
    return ""


# ════════════════════════════════════════════════════════════════════════
# 麦克风录音（VAD）
# ════════════════════════════════════════════════════════════════════════

def record_audio(skip_calibrate: bool = False) -> "bytes | None":
    """
    VAD 录音：校准噪声 → 等待说话（最多 LISTEN_TIMEOUT 秒）→ 持续录音 → 停顿结束。
    返回 WAV bytes；超时或未检测到声音返回 None。
    skip_calibrate=True 时跳过噪声校准，复用上次阈值（打断后使用）。
    """
    global _cached_threshold

    if MIC_IDX is None:
        print("[录音] 无可用麦克风"); return None

    # ── 打开麦克风（带错误处理 + 设备降级）───────────────────────
    print("[录音] 初始化麦克风...", flush=True)
    pa = pyaudio.PyAudio()
    # 优先使用选定设备，失败时依次尝试其他输入设备（errno -9999 常见于 Windows 独占模式）
    candidate_indices = [MIC_IDX] if MIC_IDX is not None else []
    candidate_indices += [i for i in range(pa.get_device_count())
                          if pa.get_device_info_by_index(i).get("maxInputChannels", 0) > 0
                          and i != MIC_IDX]
    stream = None
    for dev_idx in candidate_indices:
        try:
            stream = pa.open(
                format=pyaudio.paInt16, channels=1, rate=MIC_RATE,
                input=True, input_device_index=dev_idx,
                frames_per_buffer=MIC_CHUNK,
            )
            stream.start_stream()
            if dev_idx != MIC_IDX:
                print(f"[录音] 设备 [{MIC_IDX}] 不可用，改用 [{dev_idx}]", flush=True)
            break
        except Exception as e:
            print(f"[录音] 设备 [{dev_idx}] 打开失败: {e}", flush=True)
            stream = None
    if stream is None:
        print("[录音] 所有输入设备均打开失败")
        pa.terminate()
        return None

    pre_max   = int(MIC_RATE * PRE_BUF_SEC / MIC_CHUNK)
    pause_max = int(MIC_RATE * PAUSE_SEC   / MIC_CHUNK)

    # ── 环境噪声校准 ────────────────────────────────────────────────
    if not skip_calibrate or _cached_threshold == 0:
        print("[录音] 校准环境噪声（请保持安静）...", flush=True)
        CAL_FRAMES = int(MIC_RATE * 1.0 / MIC_CHUNK)   # 1 秒采样
        noise_rmss = []
        for _ in range(CAL_FRAMES):
            d = stream.read(MIC_CHUNK, exception_on_overflow=False)
            c = len(d) // 2
            s = struct.unpack(f"{c}h", d)
            noise_rmss.append(math.sqrt(sum(x * x for x in s) / c) if c else 0)
        avg_noise = sum(noise_rmss) / len(noise_rmss) if noise_rmss else 150
        # 阈值 = 噪声均值的 2 倍，但不低于 VAD_THRESHOLD，不高于 3000
        _cached_threshold = min(max(avg_noise * 2.0, VAD_THRESHOLD), 3000)
        print(f"[录音] 噪声基线={avg_noise:.0f}，说话触发阈值={_cached_threshold:.0f}", flush=True)

    threshold = _cached_threshold
    print(f"🎤 请说话（停顿 {PAUSE_SEC}s 后自动结束，{LISTEN_TIMEOUT:.0f}s 内无声则跳过）...", flush=True)

    rolling       = []
    consecutive   = 0
    speaking      = False
    speech_frames = []
    silent        = 0
    deadline      = time.time() + LISTEN_TIMEOUT   # 超时时刻

    try:
        while True:
            data   = stream.read(MIC_CHUNK, exception_on_overflow=False)
            count  = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            rms    = math.sqrt(sum(s * s for s in shorts) / count) if count else 0

            if not speaking:
                # 超时检测：等待阶段超过 LISTEN_TIMEOUT 则放弃
                if time.time() > deadline:
                    print(f"\n[录音] {LISTEN_TIMEOUT:.0f}s 内未检测到声音，跳过本轮", flush=True)
                    break
                rolling.append(data)
                if len(rolling) > pre_max:
                    rolling.pop(0)
                if rms > threshold:
                    consecutive += 1
                    if consecutive >= VAD_CONFIRM:
                        speaking      = True
                        speech_frames = list(rolling)
                        print("🔴 录音中...", flush=True)
                else:
                    consecutive = 0
            else:
                speech_frames.append(data)
                if rms < threshold * 0.5:
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
        try:
            stream.stop_stream(); stream.close(); pa.terminate()
        except Exception:
            pass

    if not speech_frames:
        return None
    dur = len(speech_frames) * MIC_CHUNK / MIC_RATE
    print(f"⏹ 录音结束（{dur:.1f}s）", flush=True)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(MIC_RATE)
        wf.writeframes(b"".join(speech_frames))
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════════════
# TTS 统一出口（豆包 → pyttsx3 兜底）
# ════════════════════════════════════════════════════════════════════════

def speak_and_maybe_interrupt(text: str, voice_mode: bool) -> bytes | None:
    """
    合成并播放语音。
    voice_mode=True 时支持打断，返回打断时录到的 WAV bytes 或 None。
    """
    print("🔊 合成中...", flush=True)
    pcm = tts_doubao(text)

    if pcm:
        print("🔊 播放中（说话可打断）...", flush=True)
        if voice_mode:
            return play_interruptible(pcm, TTS_RATE)
        else:
            p = _Player(pcm, TTS_RATE)
            p.start(); p.wait()
            return None
    else:
        # 兜底：pyttsx3
        print("🔊 TTS兜底（pyttsx3）...", flush=True)
        _tts_pyttsx3(text)
        return None


# ════════════════════════════════════════════════════════════════════════
# 文字聊天模式
# ════════════════════════════════════════════════════════════════════════

def run_text_mode():
    print("\n📝 文字聊天模式（输入「退出」或按 Ctrl+C 退出）\n")
    while True:
        try:
            user = input("你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！"); break
        if not user:
            continue
        if any(w in user for w in EXIT_WORDS):
            reply = "再见！期待下次聊天。"
            print(f"AI: {reply}")
            _tts_pyttsx3(reply)
            break
        reply = llm_stream(user)
        if reply:
            speak_and_maybe_interrupt(reply, voice_mode=False)


# ════════════════════════════════════════════════════════════════════════
# 语音聊天模式
# ════════════════════════════════════════════════════════════════════════

def run_voice_mode():
    print("\n🎙 语音聊天模式 | 说「退出」或按 Ctrl+C 结束\n")
    pending_wav: bytes | None = None   # 打断时录到的音频

    while True:
        try:
            # ── 获取用户语音 ───────────────────────────────────────
            if pending_wav:
                wav = pending_wav
                pending_wav = None
            else:
                wav = record_audio()
                if not wav:
                    consecutive_mic_fails += 1
                    if consecutive_mic_fails >= MAX_MIC_FAILS:
                        print(f"[麦克风] 连续 {MAX_MIC_FAILS} 次录音失败，退出语音模式", flush=True)
                        print("提示：检查麦克风驱动/隐私设置，或选择模式 1 使用文字输入", flush=True)
                        break
                    print("[录音] 未检测到声音\n", flush=True); continue
                consecutive_mic_fails = 0

            # ── ASR 识别（按当前选择库 + 自动兜底）────────────────────
            print("🔍 识别中...", flush=True)
            text = asr_recognize(wav)
            if not text:
                print("[ASR] 未识别到内容，请重试\n"); continue
            print(f"\n你: {text}")

            # ── 退出检测 ───────────────────────────────────────────
            if any(w in text for w in EXIT_WORDS):
                farewell = "再见！"
                print(f"AI: {farewell}")
                _tts_pyttsx3(farewell)
                break

            # ── LLM 对话 ───────────────────────────────────────────
            reply = llm_stream(text)
            if not reply:
                continue

            # ── TTS 播放（可打断）─────────────────────────────────
            pending_wav = speak_and_maybe_interrupt(reply, voice_mode=True)

        except KeyboardInterrupt:
            print("\n\n再见！"); break
        except Exception as e:
            print(f"[错误] {e}\n")


# ════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("   豆包 AI 聊天助手（端到端实时语音大模型）")
    print("   LLM : doubao-seed-2-0-pro-260215（流式SSE）")
    print("   ASR : 可选 doubao / moonshine / faster-whisper")
    print("   TTS : 豆包 SeedTTS 2.0（HTTP + HMAC签名）")
    print("   兜底: pyttsx3 TTS")
    print("=" * 60)
    print("  1. 文字聊天（键盘输入 + 语音播报）")
    print("  2. 语音聊天（麦克风 + 语音播报，可随时打断）")
    print("  退出：Ctrl+C  /  说退出  /  输入退出")
    print("=" * 60)
    _choose_asr_backend()
    c = input("选择模式 (1/2，回车默认文字): ").strip()
    if c == "2":
        run_voice_mode()
    else:
        run_text_mode()


if __name__ == "__main__":
    main()
