import os
import sys
import io
import threading
import time
import subprocess

# ── Windows 控制台强制使用 UTF-8 编码（解决中文乱码）────────────────────
if sys.platform == "win32":
    import os
    os.system("chcp 65001 >nul 2>&1")   # 控制台代码页切换到 UTF-8
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

import json
import datetime
import base64
import uuid
import wave
from pathlib import Path
from collections import deque
from difflib import SequenceMatcher
import requests


# ── DeepSeek 配置 ────────────────────────────────────────────────────
DEEPSEEK_API_KEY = "sk-892f8f3341d34354b8d245ade13d9269"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-v4-flash"

# ── 豆包 TTS（参考 doubao_tts_py-master HTTP）────────────────────────
DOUBAO_TTS_ENABLED = os.getenv("DOUBAO_TTS_ENABLED", "1") == "1"
DOUBAO_TTS_URL     = os.getenv("DOUBAO_TTS_URL", "https://openspeech.bytedance.com/api/v1/tts")
DOUBAO_TTS_APP_ID  = os.getenv("DOUBAO_TTS_APP_ID", "9928059183")
DOUBAO_TTS_TOKEN   = os.getenv("DOUBAO_TTS_TOKEN", "ni5KWLvTq2efj7JfJHfXO9iUv8hcOHVu")
DOUBAO_TTS_CLUSTER = os.getenv("DOUBAO_TTS_CLUSTER", "volcano_tts")
DOUBAO_TTS_VOICE   = os.getenv("DOUBAO_TTS_VOICE", "zh_female_meilinvyou_emo_v2_mars_bigtts")
DOUBAO_TTS_RATE    = int(os.getenv("DOUBAO_TTS_RATE", "24000"))
DOUBAO_TTS_ENCODING = "pcm"

MIC_MONITOR_ENABLED = os.getenv("MIC_MONITOR_ENABLED", "1") == "1"
MIC_SAVE_ENABLED = os.getenv("MIC_SAVE_ENABLED", "1") == "1"
MIC_SAVE_DIR = os.getenv("MIC_SAVE_DIR", "recordings")

DOUBAO_TTS_AVAILABLE = bool(DOUBAO_TTS_ENABLED and DOUBAO_TTS_APP_ID and DOUBAO_TTS_TOKEN)


def _ts() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def _log_step(step: str):
    print(f"[{_ts()}] {step}", flush=True)


def _log_data(tag: str, data: str):
    print(f"[{tag}] {_ts()} | {data}", flush=True)


def _mask_secret(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    if len(s) <= keep:
        return "*" * len(s)
    return s[:keep] + "***"


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _sanitize_tts_body(body: dict) -> dict:
    b = json.loads(json.dumps(body, ensure_ascii=False))
    if "app" in b and isinstance(b["app"], dict):
        b["app"]["token"] = _mask_secret(str(b["app"].get("token", "")))
    return b


def _sanitize_tts_resp_text(text: str) -> str:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], str):
            obj["data"] = f"<base64:{len(obj['data'])}>"
        return _safe_json(obj)
    except Exception:
        return text



if DOUBAO_TTS_ENABLED and not DOUBAO_TTS_AVAILABLE:
    print("[提示] 豆包TTS配置不完整，将回退到本地 pyttsx3")
if DOUBAO_TTS_AVAILABLE:
    print(f"[语音] 豆包TTS已启用，音色: {DOUBAO_TTS_VOICE}")



# ── 语音依赖（可选，未安装时自动降级为文字模式）──────────────────────────
try:
    import speech_recognition as sr
    import pyaudio as _pyaudio

    def _registry_chinese_names() -> list:
        """从注册表收集所有捕获端点的中文友好名称（REG_SZ 原生 Unicode，无编码问题）"""
        import winreg
        REG_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
        FRIENDLY  = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
        seen, names = set(), []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH) as base:
                i = 0
                while True:
                    try:
                        guid = winreg.EnumKey(base, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(base, guid + r"\Properties") as props:
                            v, _ = winreg.QueryValueEx(props, FRIENDLY)
                            # 只保留含中文字符的名称
                            if any("\u4e00" <= c <= "\u9fff" for c in v) and v not in seen:
                                seen.add(v)
                                names.append(v)
                    except (FileNotFoundError, OSError):
                        pass
        except Exception:
            pass
        return names

    def _repair_device_name(pa_name: str, cn_reg: list) -> str:
        """修复 PyAudio 设备名中的乱码中文前缀。
        格式：{中文前缀} ({英文设备类型})
        策略：
          1. 前缀已在注册表中文名列表中 → 无需修复
          2. 否则用英文后缀关键词在注册表中文名里查找替代
        """
        import re
        m = re.match(r'^(.*?)\s*(\([^)]+\))\s*$', pa_name)
        if not m:
            return pa_name
        prefix = m.group(1).strip()
        paren  = m.group(2)            # e.g. "(Realtek HD Audio Line input)"
        paren_lower = paren.lower()

        # 前缀已正确，直接返回
        if prefix in cn_reg:
            return pa_name

        # 英文类型关键词 → 对应中文名中应包含的子串
        KEYWORD_MAP = [
            (["microphone", "mic"],              ["麦克风", "话筒"]),
            (["line input", "line in", "line"],  ["线路输入", "线路"]),
            (["stereo mix", "stereo"],           ["立体声混音", "立体声"]),
            (["headphone", "headset"],           ["耳机"]),
            (["speaker"],                        ["扬声器"]),
            (["front"],                          ["前置"]),
            (["rear"],                           ["后置"]),
            (["center"],                         ["中置"]),
            (["subwoofer"],                      ["低音"]),
        ]
        for en_keys, cn_subs in KEYWORD_MAP:
            if any(k in paren_lower for k in en_keys):
                for cn_sub in cn_subs:
                    for rn in cn_reg:
                        if cn_sub in rn:
                            return f"{rn} {paren}"
        return pa_name   # 无法修复则原样返回

    def _list_microphones():
        """返回 [(pyaudio_index, name), ...] 的可用麦克风列表。
        Windows 上用注册表 Unicode 名称修复 PyAudio 可能乱码的设备名前缀。
        """
        pa = _pyaudio.PyAudio()
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devices.append((i, info["name"]))
        pa.terminate()

        if sys.platform == "win32":
            try:
                cn_reg = _registry_chinese_names()
                devices = [(idx, _repair_device_name(name, cn_reg))
                           for idx, name in devices]
            except Exception as e:
                print(f"[语音] 设备名修复失败: {e}")

        return devices

    def _select_microphone_index():
        """选择最佳麦克风设备，优先级: WASAPI > DirectSound > MME > WDM-KS。
        返回 (device_index, native_sample_rate) 或 (None, 16000)。
        """
        pa = _pyaudio.PyAudio()
        buckets = {"wasapi": [], "directsound": [], "mme": [], "other": []}
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) <= 0:
                continue
            api_name = pa.get_host_api_info_by_index(info["hostApi"]).get("name", "").lower()
            native_rate = int(info.get("defaultSampleRate", 16000))
            entry = (i, info.get("name", ""), native_rate)
            if "wasapi" in api_name:
                buckets["wasapi"].append(entry)
            elif "directsound" in api_name:
                buckets["directsound"].append(entry)
            elif "mme" in api_name:
                buckets["mme"].append(entry)
            else:
                buckets["other"].append(entry)
        pa.terminate()

        for key in ("wasapi", "directsound", "mme", "other"):
            if buckets[key]:
                idx, name, rate = buckets[key][0]
                print(f"[语音] 麦克风: [{idx}] {name}  API={key.upper()}  rate={rate}Hz")
                return idx, rate
        return None, 16000

    _MIC_DEVICE_INDEX, _MIC_NATIVE_RATE = _select_microphone_index()
    VOICE_INPUT_AVAILABLE = _MIC_DEVICE_INDEX is not None
    if not VOICE_INPUT_AVAILABLE:
        print("[提示] 未检测到可用麦克风设备，语音输入不可用。")
except ImportError:
    VOICE_INPUT_AVAILABLE = False
    _MIC_DEVICE_INDEX = None
    _MIC_NATIVE_RATE  = 16000
    print("[提示] 未安装 speech_recognition / pyaudio，语音输入不可用。"
          "可运行: pip install SpeechRecognition pyaudio")

try:
    import pyttsx3
    _tts_engine = pyttsx3.init()
    # 列出并选择中文语音
    _selected_voice = None
    for voice in _tts_engine.getProperty("voices"):
        if "chinese" in voice.name.lower() or "zh" in voice.id.lower():
            _tts_engine.setProperty("voice", voice.id)
            _selected_voice = voice.name
            break
    if _selected_voice:
        print(f"[语音] TTS 已选中文语音: {_selected_voice}")
    else:
        # 未找到中文语音，列出所有可用语音供参考
        all_voices = [v.name for v in _tts_engine.getProperty("voices")]
        print(f"[语音] 未找到中文 TTS 语音，将使用默认语音。当前可用语音: {all_voices}")
    _tts_engine.setProperty("rate", 180)
    _tts_engine.setProperty("volume", 1.0)
    VOICE_OUTPUT_AVAILABLE = True
except ImportError:
    VOICE_OUTPUT_AVAILABLE = False
    print("[提示] 未安装 pyttsx3，语音输出不可用。可运行: pip install pyttsx3")
except Exception as e:
    VOICE_OUTPUT_AVAILABLE = False
    print(f"[提示] pyttsx3 初始化失败: {e}，语音输出不可用。")

if DOUBAO_TTS_AVAILABLE:
    VOICE_OUTPUT_AVAILABLE = True


# ── Qwen3-ASR / Moonshine / Whisper 语音识别模型──────────────────────
QWEN3_ASR_ENABLED = os.getenv("QWEN3_ASR_ENABLED", "1") == "1"
QWEN3_ASR_MODEL = os.getenv("QWEN3_ASR_MODEL", "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch")
# Moonshine 模型选项: tiny / base
MOONSHINE_MODEL_SIZE = os.getenv("MOONSHINE_MODEL_SIZE", "base")
MOONSHINE_LANGUAGE = os.getenv("MOONSHINE_LANGUAGE", "zh")
# Whisper 模型选项: tiny / base / small / medium / large-v3
WHISPER_MODEL_SIZE = "small"
EXIT_WORDS = {
    "退出", "退出程序", "关闭程序", "关闭聊天", "关闭对话",
    "再见", "结束", "拜拜", "quit", "exit", "bye",
}
EXIT_EXACT_WORDS = {"quit", "exit", "退出"}


def _normalize_command_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = "".join(t.split())
    for ch in "，。！？、,.!?;；:：\"'“”‘’（）()[]【】{}<>《》-—_":
        t = t.replace(ch, "")
    return t


def _open_netease_cloudmusic() -> tuple[bool, str]:
    candidates = [
        r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe",
        r"C:\Program Files\Netease\CloudMusic\cloudmusic.exe",
        r"%LOCALAPPDATA%\Programs\Netease\CloudMusic\cloudmusic.exe",
    ]

    for raw_path in candidates:
        exe_path = os.path.expandvars(raw_path)
        if os.path.exists(exe_path):
            try:
                os.startfile(exe_path)
                return True, exe_path
            except Exception:
                pass

    try:
        subprocess.Popen(["cmd", "/c", "start", "", "orpheus://"], shell=False)
        return True, "orpheus://"
    except Exception:
        pass

    return False, ""


def _open_notepadpp() -> tuple[bool, str]:
    candidates = [
        r"D:\Program\Notepad++\notepad++.exe",
        r"C:\Program Files\Notepad++\notepad++.exe",
        r"C:\Program Files (x86)\Notepad++\notepad++.exe",
        r"%LOCALAPPDATA%\Programs\Notepad++\notepad++.exe",
    ]

    for raw_path in candidates:
        exe_path = os.path.expandvars(raw_path)
        if os.path.exists(exe_path):
            try:
                os.startfile(exe_path)
                return True, exe_path
            except Exception:
                pass

    try:
        subprocess.Popen(["notepad++"], shell=False)
        return True, "notepad++"
    except Exception:
        pass

    return False, ""


try:
    import numpy as _np
except Exception:
    _np = None

if QWEN3_ASR_ENABLED:
    print("[语音] 正在加载 Qwen3-ASR 模型（优先识别）...", flush=True)
try:
    if QWEN3_ASR_ENABLED:
        from funasr import AutoModel as _FunASRAutoModel
        _qwen3_asr_model = _FunASRAutoModel(model=QWEN3_ASR_MODEL)
        QWEN3_ASR_AVAILABLE = _np is not None
        if QWEN3_ASR_AVAILABLE:
            print(f"[语音] Qwen3-ASR 加载完成: {QWEN3_ASR_MODEL}", flush=True)
        else:
            _qwen3_asr_model = None
            print("[提示] Qwen3-ASR 依赖 numpy 不可用，已禁用。可运行: pip install numpy", flush=True)
    else:
        QWEN3_ASR_AVAILABLE = False
        _qwen3_asr_model = None
except Exception as e:
    QWEN3_ASR_AVAILABLE = False
    _qwen3_asr_model = None
    print(f"[提示] Qwen3-ASR 加载失败: {e}，将回退到 Moonshine/Google/Whisper", flush=True)
    print("[提示] 可运行: pip install funasr", flush=True)

print("[语音] 正在加载 Moonshine 模型（次优先识别）...", flush=True)
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
    MOONSHINE_AVAILABLE = _np is not None
    if MOONSHINE_AVAILABLE:
        print(f"[语音] Moonshine {MOONSHINE_MODEL_SIZE}-{MOONSHINE_LANGUAGE} 加载完成（优先）", flush=True)
    else:
        _moonshine = None
        print("[提示] Moonshine 依赖 numpy 不可用，已跳过。可运行: pip install numpy", flush=True)
except Exception as e:
    MOONSHINE_AVAILABLE = False
    _moonshine = None
    print(f"[提示] Moonshine 加载失败: {e}，将回退到 Google/Whisper")
    print("[提示] 可运行: pip install moonshine-voice", flush=True)

try:
    from faster_whisper import WhisperModel as _WhisperModel
    print(f"[语音] 正在加载 faster-whisper {WHISPER_MODEL_SIZE} 模型（Moonshine/Google 失败时兜底）...", flush=True)
    _whisper_model = _WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    WHISPER_AVAILABLE = _np is not None
    if WHISPER_AVAILABLE:
        print(f"[语音] faster-whisper {WHISPER_MODEL_SIZE} 加载完成")
    else:
        _whisper_model = None
        print("[提示] faster-whisper 依赖 numpy 不可用，已禁用。可运行: pip install numpy")
except ImportError:
    WHISPER_AVAILABLE = False
    _whisper_model = None
    print("[提示] 未安装 faster-whisper，Moonshine/Google 失败时将无离线兜底。"
          "可运行: pip install faster-whisper")
except Exception as e:
    WHISPER_AVAILABLE = False
    _whisper_model = None
    print(f"[提示] faster-whisper 加载失败: {e}，Moonshine/Google 失败时将无离线兜底。")


def _recognize_qwen3_asr(audio_data) -> str:
    if not QWEN3_ASR_AVAILABLE or _qwen3_asr_model is None or _np is None:
        return ""
    try:
        if isinstance(audio_data, (bytes, bytearray)):
            raw_pcm = bytes(audio_data)
        else:
            raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        audio_f32 = _np.frombuffer(raw_pcm, dtype=_np.int16).astype(_np.float32) / 32768.0
        result = _qwen3_asr_model.generate(input=audio_f32, sampling_rate=16000)
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                return str(first.get("text", "")).strip()
            return str(first).strip()
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        return str(result).strip() if result else ""
    except Exception as e:
        print(f"[语音] Qwen3-ASR 识别异常: {e}")
        return ""


def _recognize_moonshine(audio_data) -> str:
    if not MOONSHINE_AVAILABLE or _moonshine is None or _np is None:
        return ""
    try:
        if isinstance(audio_data, (bytes, bytearray)):
            raw_pcm = bytes(audio_data)
            sample_rate = 16000
        else:
            raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
            sample_rate = 16000
        audio_f32 = _np.frombuffer(raw_pcm, dtype=_np.int16).astype(_np.float32) / 32768.0
        transcript = _moonshine.transcribe_without_streaming(audio_f32.tolist(), sample_rate=sample_rate)
        lines = getattr(transcript, "lines", []) or []
        return "".join((ln.text or "") for ln in lines).strip()
    except Exception as e:
        print(f"[语音] Moonshine 识别异常: {e}")
        return ""


def _recognize(audio_data) -> str:
    """统一语音识别入口：Qwen3-ASR 优先，失败后回退 Moonshine、Google、faster-whisper。"""
    # ── Qwen3-ASR（最高优先）────────────────────────────────────────
    text = _recognize_qwen3_asr(audio_data)
    if text:
        print("[ASR] 使用: Qwen3-ASR", flush=True)
        return text

    # ── Moonshine（次优先）───────────────────────────────────────────
    text = _recognize_moonshine(audio_data)
    if text:
        print("[ASR] 使用: moonshine_voice", flush=True)
        return text

    # ── Google 在线识别（再次优先）──────────────────────────────────
    recognizer = sr.Recognizer()
    MAX_RETRIES = 3
    google_error = None
    if isinstance(audio_data, (bytes, bytearray)):
        audio_for_google = sr.AudioData(audio_data, 16000, 2)
    else:
        audio_for_google = audio_data
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            text = recognizer.recognize_google(audio_for_google, language="zh-CN")
            if text:
                print("[ASR] 使用: Google", flush=True)
            return text
        except sr.UnknownValueError:
            break
        except sr.RequestError as e:
            google_error = e
            if attempt < MAX_RETRIES:
                print(f"[语音] Google 识别出错（第{attempt}次，重试中）: {e}")
                time.sleep(1)
    print(f"[语音] Google 识别失败，切换到 faster-whisper 离线兜底: {google_error}")

    # ── faster-whisper 离线识别（兜底）──────────────────────────────
    if WHISPER_AVAILABLE and _whisper_model is not None and _np is not None:
        try:
            if isinstance(audio_data, (bytes, bytearray)):
                raw_pcm = audio_data
            else:
                raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
            audio_np = _np.frombuffer(raw_pcm, dtype=_np.int16).astype(_np.float32) / 32768.0
            segments, _ = _whisper_model.transcribe(
                audio_np,
                language="zh",
                beam_size=3,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = "".join(seg.text for seg in segments).strip()
            if text:
                print("[ASR] 使用: faster-whisper", flush=True)
            return text
        except Exception as e:
            print(f"[语音] faster-whisper 识别异常: {e}")
    else:
        print("[语音] faster-whisper 不可用，识别失败")
    return ""

def _open_monitor_output(pa, rate: int):
    if not MIC_MONITOR_ENABLED:
        return None
    try:
        return pa.open(format=_pyaudio.paInt16, channels=1, rate=rate, output=True)
    except Exception as e:
        print(f"[语音] 输入监听播放不可用: {e}")
        return None


def _monitor_chunk(out_stream, chunk: bytes):
    if out_stream is None:
        return
    try:
        out_stream.write(chunk)
    except Exception:
        pass


def _save_input_wav(frames: list[bytes], rate: int, sample_width: int, tag: str):
    if not MIC_SAVE_ENABLED or not frames:
        return None
    try:
        save_dir = Path(MIC_SAVE_DIR)
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = save_dir / f"{tag}_{ts}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(sample_width)
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))
        print(f"[录音] 已保存: {path}", flush=True)
        _log_step(f"语音文件保存完成: {path}")
        return path
    except Exception as e:
        print(f"[录音] 保存失败: {e}")
        return None


def _play_input_once(frames: list[bytes], rate: int):
    if not frames:
        return
    try:
        _log_step("语音回放开始")
        pa = _pyaudio.PyAudio()
        out = pa.open(format=_pyaudio.paInt16, channels=1, rate=rate, output=True)
        out.write(b"".join(frames))
        out.stop_stream()
        out.close()
        pa.terminate()
        _log_step("语音回放完成")
    except Exception as e:
        print(f"[语音] 输入回放失败: {e}")


def _save_and_play_input_once(frames: list[bytes], rate: int, sample_width: int, tag: str):
    _log_step("语音文件保存开始")
    _save_input_wav(frames, rate, sample_width, tag)
    _play_input_once(frames, rate)


def listen_from_microphone() -> str:
    """从麦克风录音并识别为文字，失败返回空字符串。

    使用 PyAudio 回调模式（非阻塞）：回调把每帧放入 Queue，
    主线程用 queue.get(timeout) 读取，驱动无论是否正常都不会永久阻塞。
    """
    import queue as _queue, struct as _struct, math as _math

    RATE         = _MIC_NATIVE_RATE   # 使用设备原生采样率，避免重采样失败
    CHUNK        = 1024
    SAMPLE_WIDTH = 2
    LISTEN_SEC   = 10
    PAUSE_SEC    = 2.5
    MAX_REC_SEC  = 60

    # 候选设备：首选 _MIC_DEVICE_INDEX（已按 WASAPI > DS > MME 优先级选出）
    candidates = [_MIC_DEVICE_INDEX]

    for dev_idx in candidates:
        audio_q = _queue.Queue()

        def _cb(in_data, frame_count, time_info, status, _q=audio_q):
            _q.put(in_data)
            return (None, _pyaudio.paContinue)

        pa     = _pyaudio.PyAudio()
        stream = None
        monitor_stream = None
        try:
            stream = pa.open(
                format=_pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=CHUNK,
                stream_callback=_cb,
            )
            stream.start_stream()
            monitor_stream = None
            # monitor_stream = _open_monitor_output(pa, RATE)  # 已按需求注释：边录边播
        except Exception as e:
            print(f"[麦克风] 设备 {dev_idx} 打开失败: {e}", flush=True)
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass
            pa.terminate()
            continue

        print(f"🎤 请说话 (设备={dev_idx} {RATE}Hz，{LISTEN_SEC}s无声超时，停顿{PAUSE_SEC}s结束)",
              flush=True)

        try:
            # ── 校准环境噪音 0.5s ─────────────────────────────────────
            cal, cal_deadline = [], time.time() + 2.0
            cal_target = max(1, int(RATE * 0.5 / CHUNK))
            while len(cal) < cal_target and time.time() < cal_deadline:
                try:
                    d = audio_q.get(timeout=0.2)
                    cal.append(d)
                    # _monitor_chunk(monitor_stream, d)  # 已按需求注释：边录边播
                except _queue.Empty:
                    pass

            if not cal:
                print("[麦克风] 回调无数据，设备不可用", flush=True)
                return ""

            raw_cal = b"".join(cal)
            cnt     = len(raw_cal) // 2
            shorts  = _struct.unpack(f"{cnt}h", raw_cal)
            ambient = _math.sqrt(sum(s * s for s in shorts) / cnt)
            thr     = max(ambient * 1.5, 300)
            print(f"  噪音阈值={thr:.0f}", flush=True)

            # ── 等待说话开始 ──────────────────────────────────────────
            deadline = time.time() + LISTEN_SEC
            frames, speech = [], False
            while time.time() < deadline:
                try:
                    chunk = audio_q.get(timeout=0.1)
                except _queue.Empty:
                    continue
                # _monitor_chunk(monitor_stream, chunk)  # 已按需求注释：边录边播
                cnt    = len(chunk) // 2
                shorts = _struct.unpack(f"{cnt}h", chunk)
                rms    = _math.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms > thr:
                    frames = [chunk]
                    speech = True
                    break

            if not speech:
                print(f"[语音] {LISTEN_SEC}s 内未检测到声音", flush=True)
                return ""

            # ── 录音直到静音 ──────────────────────────────────────────
            silence_cnt  = 0
            pause_chunks = max(1, int(RATE * PAUSE_SEC / CHUNK))
            deadline2    = time.time() + MAX_REC_SEC
            while time.time() < deadline2:
                try:
                    chunk = audio_q.get(timeout=0.5)
                except _queue.Empty:
                    break
                frames.append(chunk)
                # _monitor_chunk(monitor_stream, chunk)  # 已按需求注释：边录边播
                cnt    = len(chunk) // 2
                shorts = _struct.unpack(f"{cnt}h", chunk)
                rms    = _math.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms < thr * 0.6:
                    silence_cnt += 1
                    if silence_cnt >= pause_chunks:
                        break
                else:
                    silence_cnt = 0

            # ── 识别 ─────────────────────────────────────────────────
            _save_and_play_input_once(frames, RATE, SAMPLE_WIDTH, "listen")
            print("🔍 识别中...", flush=True)
            _log_step("语音识别开始")
            audio_data = sr.AudioData(b"".join(frames), RATE, SAMPLE_WIDTH)
            try:
                text = _recognize(audio_data)
                _log_step("语音识别完成")
                if text:
                    print(f"你（语音）: {text}")
                    return text
                else:
                    print("[语音] 未能识别", flush=True)
                    return ""
            except Exception as e:
                print(f"[语音] 识别异常: {e}")
                return ""

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[麦克风] 设备 {dev_idx} 运行异常: {e}", flush=True)
            continue
        finally:
            try:
                if monitor_stream:
                    monitor_stream.stop_stream()
                    monitor_stream.close()
            except Exception:
                pass
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            pa.terminate()

    print("[麦克风] 无可用输入设备", flush=True)
    return ""


# ── 豆包 TTS（HTTP）───────────────────────────────────────────────────
def _tts_doubao_http(text: str) -> bytes | None:
    if not DOUBAO_TTS_AVAILABLE:
        return None
    body = {
        "app": {
            "appid": DOUBAO_TTS_APP_ID,
            "token": DOUBAO_TTS_TOKEN,
            "cluster": DOUBAO_TTS_CLUSTER,
        },
        "user": {"uid": "deepseek_user"},
        "audio": {
            "voice_type": DOUBAO_TTS_VOICE,
            "encoding": DOUBAO_TTS_ENCODING,
            "rate": DOUBAO_TTS_RATE,
            "channel": 1,
            "bits": 16,
            "speed_ratio": 1.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer;{DOUBAO_TTS_TOKEN}",
    }
    _log_step("TTS请求")
    try:
        resp = requests.post(DOUBAO_TTS_URL, headers=headers, json=body, timeout=20)
        print(f"[TTS响应] {_ts()}", flush=True)
        if resp.status_code != 200:
            print(f"[TTS] 豆包HTTP {resp.status_code}: {resp.text[:160]}")
            return None
        data = resp.json()
        if data.get("code") != 3000 or not data.get("data"):
            print(f"[TTS] 豆包返回错误: {data}")
            return None
        return base64.b64decode(data["data"])
    except Exception as e:
        print(f"[TTS] 豆包请求失败: {e}")
        return None


def _play_pcm(pcm: bytes, rate: int):
    pa = _pyaudio.PyAudio()
    stream = pa.open(format=_pyaudio.paInt16, channels=1, rate=rate, output=True)
    try:
        stream.write(pcm)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


# ── 语音输出 ─────────────────────────────────────────────────────────
def _build_tts_script(text: str) -> str:
    """构建 pyttsx3 子进程执行脚本"""
    voice_line = (
        f"[e.setProperty('voice',v.id) for v in e.getProperty('voices') if v.name=={repr(_selected_voice)}];"
        if _selected_voice else ""
    )
    return (
        "import pyttsx3;"
        "e=pyttsx3.init();"
        + voice_line
        + "e.setProperty('rate',180);"
        "e.setProperty('volume',1.0);"
        f"e.say({repr(text)});"
        "e.runAndWait()"
    )


def speak(text: str):
    """不可打断的语音播放（用于退出提示等简短语句）"""
    _log_step("TTS播放开始")
    if DOUBAO_TTS_AVAILABLE:
        print(f"[TTS] 使用: 豆包HTTP ({DOUBAO_TTS_VOICE})", flush=True)
        pcm = _tts_doubao_http(text)
        if pcm:
            try:
                _play_pcm(pcm, DOUBAO_TTS_RATE)
                _log_step("TTS播放完成")
                return
            except Exception as e:
                print(f"[语音] 豆包TTS播放失败，回退pyttsx3: {e}")
        else:
            print("[TTS] 豆包TTS无可用音频，回退到 pyttsx3", flush=True)

    if not VOICE_OUTPUT_AVAILABLE:
        print("[TTS] 不可用: 豆包与 pyttsx3 都不可用", flush=True)
        return

    print("[TTS] 使用: pyttsx3", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", _build_tts_script(text)]
    )
    try:
        proc.wait(timeout=8)
        _log_step("TTS播放完成")
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
    except Exception as e:
        print(f"[语音] 朗读失败: {e}")


def _announce_tts_status(mode: str):
    if mode in ("full", "text+voice"):
        if DOUBAO_TTS_AVAILABLE:
            print(f"[TTS] 当前优先: 豆包HTTP ({DOUBAO_TTS_VOICE})，失败回退 pyttsx3", flush=True)
        elif VOICE_OUTPUT_AVAILABLE:
            print("[TTS] 当前使用: pyttsx3", flush=True)
        else:
            print("[TTS] 当前状态: 不可用", flush=True)


























def _capture_interrupt_speech(is_playing, stop_playback) -> str:
    """监听打断并在打断后采集语音，返回识别文本。"""
    if not VOICE_INPUT_AVAILABLE:
        return ""
    try:
        import queue as _q, struct as _s, math as _m
        RATE           = _MIC_NATIVE_RATE
        CHUNK          = 1024
        SAMPLE_WIDTH   = 2
        VAD_THRESHOLD  = 900
        CONFIRM_FRAMES = 3
        PRE_BUFFER_SEC = 1.0
        PAUSE_SEC      = 2.5
        ARM_DELAY_SEC  = 0.35
        _arm_time      = time.time() + ARM_DELAY_SEC

        pre_buf_max = int(RATE * PRE_BUFFER_SEC / CHUNK)
        pause_max   = int(RATE * PAUSE_SEC / CHUNK)

        audio_q = _q.Queue()

        def _cb(in_data, frame_count, time_info, status, _aq=audio_q):
            _aq.put(in_data)
            return (None, _pyaudio.paContinue)

        pa = _pyaudio.PyAudio()
        monitor_stream = None
        # monitor_stream = _open_monitor_output(pa, RATE)  # 已按需求注释：边录边播
        stream = pa.open(
            format=_pyaudio.paInt16, channels=1, rate=RATE,
            input=True, input_device_index=_MIC_DEVICE_INDEX,
            frames_per_buffer=CHUNK,
            stream_callback=_cb,
        )
        stream.start_stream()

        time.sleep(0.4)

        rolling = []
        consecutive = 0
        interrupted = False

        while is_playing():
            try:
                data = audio_q.get(timeout=0.1)
            except _q.Empty:
                continue
            rolling.append(data)
            # _monitor_chunk(monitor_stream, data)  # 已按需求注释：边录边播
            if len(rolling) > pre_buf_max:
                rolling.pop(0)
            cnt = len(data) // 2
            shorts = _s.unpack(f"{cnt}h", data)
            rms = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
            if time.time() < _arm_time:
                continue
            if rms > VAD_THRESHOLD:
                consecutive += 1
                if consecutive >= CONFIRM_FRAMES:
                    interrupted = True
                    break
            else:
                consecutive = 0

        if not interrupted:
            try:
                if monitor_stream:
                    monitor_stream.stop_stream()
                    monitor_stream.close()
            except Exception:
                pass
            stream.stop_stream(); stream.close(); pa.terminate()
            return ""

        stop_playback()
        print("\n[打断] 正在聆听...", flush=True)

        speech_frames = list(rolling)
        silent = 0
        max_frames = int(RATE * 60 / CHUNK)

        while len(speech_frames) < max_frames:
            try:
                data = audio_q.get(timeout=0.5)
            except _q.Empty:
                break
            speech_frames.append(data)
            # _monitor_chunk(monitor_stream, data)  # 已按需求注释：边录边播
            cnt = len(data) // 2
            shorts = _s.unpack(f"{cnt}h", data)
            rms = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
            if rms < VAD_THRESHOLD * 0.6:
                silent += 1
                if silent >= pause_max:
                    break
            else:
                silent = 0

        _save_and_play_input_once(speech_frames, RATE, SAMPLE_WIDTH, "interrupt")
        try:
            if monitor_stream:
                monitor_stream.stop_stream()
                monitor_stream.close()
        except Exception:
            pass
        stream.stop_stream(); stream.close(); pa.terminate()

        raw = b"".join(speech_frames)
        audio_data = sr.AudioData(raw, RATE, SAMPLE_WIDTH)
        print("🔍 识别中...", flush=True)
        _log_step("语音识别开始")
        text = _recognize(audio_data)
        _log_step("语音识别完成")
        if text:
            print(f"你（语音）: {text}")
            return text
        print("[语音] 未能识别，请重新说话。")
        return ""
    except Exception as e:
        print(f"[语音] 打断监听异常: {e}")
        return ""


def speak_interruptible(text: str) -> str:
    """可打断的语音播放（豆包HTTP优先，失败回退 pyttsx3）。"""
    if not VOICE_OUTPUT_AVAILABLE:
        return ""

    if DOUBAO_TTS_AVAILABLE:
        pcm = _tts_doubao_http(text)
        if pcm:
            print(f"[TTS] 使用: 豆包HTTP（可打断）({DOUBAO_TTS_VOICE})", flush=True)

            class _Player:
                def __init__(self, data: bytes, rate: int):
                    self._data = data
                    self._rate = rate
                    self._stop = threading.Event()
                    self._done = threading.Event()
                    self._t = threading.Thread(target=self._run, daemon=True)

                def _run(self):
                    pa = _pyaudio.PyAudio()
                    stream = pa.open(format=_pyaudio.paInt16, channels=1, rate=self._rate, output=True)
                    pos, chunk = 0, 4096
                    try:
                        while pos < len(self._data) and not self._stop.is_set():
                            stream.write(self._data[pos:pos + chunk])
                            pos += chunk
                    finally:
                        stream.stop_stream(); stream.close(); pa.terminate()
                        self._done.set()

                def start(self):
                    self._t.start()

                def interrupt(self):
                    self._stop.set()

                def is_playing(self):
                    return self._t.is_alive() and not self._done.is_set()

                def wait(self, timeout=None):
                    self._done.wait(timeout)

            player = _Player(pcm, DOUBAO_TTS_RATE)
            player.start()
            time.sleep(0.25)
            captured = _capture_interrupt_speech(player.is_playing, player.interrupt)
            player.wait(timeout=120)
            return captured
        print("[TTS] 豆包TTS无可用音频，回退到 pyttsx3（可打断）", flush=True)

    print("[TTS] 使用: pyttsx3（可打断）", flush=True)
    proc = subprocess.Popen([sys.executable, "-c", _build_tts_script(text)])

    def _is_playing():
        return proc.poll() is None

    def _stop_playback():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    captured = _capture_interrupt_speech(_is_playing, _stop_playback)
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
    return captured



# ── AI 聊天（流式） ───────────────────────────────────────────────────
MAX_CONTEXT_MESSAGES = 6
MAX_MESSAGE_CHARS = 500
MAX_REQUEST_CHARS = 2200
MAX_RESPONSE_TOKENS = 220

TTS_INPUT_COOLDOWN_SECONDS = 2.0
SIMILARITY_BLOCK_THRESHOLD = 0.86
REQUESTS_PER_MINUTE_LIMIT = 8


def _trim_message_text(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    half = MAX_MESSAGE_CHARS // 2
    return f"{text[:half]}\n...[已截断 {len(text) - MAX_MESSAGE_CHARS} 字符]...\n{text[-half:]}"


def _build_messages_for_request(conversation_history: list[dict]) -> list[dict]:
    if not conversation_history:
        return []
    system_msg = conversation_history[0] if conversation_history[0].get("role") == "system" else None
    non_system = [m for m in conversation_history if m.get("role") != "system"]
    recent = non_system[-MAX_CONTEXT_MESSAGES:]

    messages = []
    total_chars = 0

    if system_msg:
        sys_text = _trim_message_text(system_msg.get("content", ""))
        messages.append({"role": "system", "content": sys_text})
        total_chars += len(sys_text)

    selected = []
    for msg in reversed(recent):
        content = _trim_message_text(msg.get("content", ""))
        if total_chars + len(content) > MAX_REQUEST_CHARS:
            continue
        selected.append({"role": msg.get("role", "user"), "content": content})
        total_chars += len(content)

    messages.extend(reversed(selected))
    return messages


def _print_request_messages(messages: list[dict]):
    merged = "\n\n".join(f"[{m.get('role', 'user')}]\n{m.get('content', '')}" for m in messages)
    print("\n[发送给AI的字符串开始]", flush=True)
    print(merged, flush=True)
    print("[发送给AI的字符串结束]\n", flush=True)


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _prune_request_times(request_times: deque, now_ts: float):
    while request_times and (now_ts - request_times[0]) > 60:
        request_times.popleft()


def _can_send_request(request_times: deque) -> tuple[bool, int]:
    now_ts = time.time()
    _prune_request_times(request_times, now_ts)
    remaining = REQUESTS_PER_MINUTE_LIMIT - len(request_times)
    return remaining > 0, max(0, remaining)


def chat(conversation_history, user_input):
    """发送消息并流式获取 DeepSeek 回复"""
    conversation_history.append({"role": "user", "content": user_input})
    messages_for_request = _build_messages_for_request(conversation_history)
    _print_request_messages(messages_for_request)
    _log_step("AI请求开始")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages_for_request,
        "stream": True,
        "max_tokens": MAX_RESPONSE_TOKENS,
        "thinking": {"type": "disabled"},
    }

    response = requests.post(DEEPSEEK_API_URL, headers=headers,
                             data=json.dumps(payload), stream=True)
    _log_step("AI请求完成")
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

    print(f"\n[{_ts()}] AI: ", end="", flush=True)
    full_reply = ""
    try:
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8").strip()
            if text.startswith("data: "):
                text = text[6:]
            if text == "[DONE]":
                break
            try:
                chunk = json.loads(text)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    print(content, end="", flush=True)
                    full_reply += content
            except json.JSONDecodeError:
                continue
    except Exception as e:
        print(f"\n[提示] 网络传输中断: {e}，已收到部分回复。", flush=True)
    print()
    _log_step("AI回复完成")

    if full_reply:
        conversation_history.append({"role": "assistant", "content": full_reply})
    return conversation_history, full_reply


# ── 选择聊天模式 ──────────────────────────────────────────────────────
def choose_mode() -> str:
    """
    返回值：
      'text'       仅文字
      'voice'      仅语音
      'text+voice' 文字输入 + 语音播报
      'voice+text' 语音输入 + 文字输出（无朗读）
      'full'       语音输入 + 语音播报
    """
    print("\n请选择聊天模式：")
    print(f"  [当前状态] 语音输入: {'✅ 可用' if VOICE_INPUT_AVAILABLE else '❌ 不可用'}  "
          f"语音播报: {'✅ 可用' if VOICE_OUTPUT_AVAILABLE else '❌ 不可用'}")
    print("  1. 纯文字（默认）")
    if VOICE_INPUT_AVAILABLE and VOICE_OUTPUT_AVAILABLE:
        print("  2. 纯语音（语音输入 + 语音播报）")
        print("  3. 文字输入 + 语音播报")
        print("  4. 语音输入 + 文字输出")
    elif VOICE_INPUT_AVAILABLE:
        print("  2. 语音输入 + 文字输出（pyttsx3 未安装，仅文字输出）")
    elif VOICE_OUTPUT_AVAILABLE:
        print("  2. 文字输入 + 语音播报（speech_recognition 未安装，仅文字输入）")

    choice = input("输入编号（直接回车默认文字模式）: ").strip()

    if choice == "2":
        if VOICE_INPUT_AVAILABLE and VOICE_OUTPUT_AVAILABLE:
            return "full"
        elif VOICE_INPUT_AVAILABLE:
            return "voice+text"
        elif VOICE_OUTPUT_AVAILABLE:
            return "text+voice"
    elif choice == "3" and VOICE_INPUT_AVAILABLE and VOICE_OUTPUT_AVAILABLE:
        return "text+voice"
    elif choice == "4" and VOICE_INPUT_AVAILABLE and VOICE_OUTPUT_AVAILABLE:
        return "voice+text"
    return "text"


# ── 主函数 ────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("        欢迎使用 DeepSeek 聊天助手")
    print("=" * 50)
    print("输入 'quit' 或 'exit' 退出程序")
    print("输入 'clear' 清除对话历史")
    print("输入 'history' 查看历史记录")
    print("输入 'mode' 切换聊天模式")
    print("=" * 50)

    mode = choose_mode()
    mode_names = {
        "text":       "纯文字",
        "full":       "纯语音（语音输入 + 语音播报）",
        "text+voice": "文字输入 + 语音播报",
        "voice+text": "语音输入 + 文字输出",
    }
    print(f"\n当前模式：{mode_names.get(mode, mode)}")
    _announce_tts_status(mode)
    if mode == "full" and VOICE_OUTPUT_AVAILABLE:
        speak("我们开始聊天吧")

    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    system_prompt = (f"你是一个智能助手，当前时间是 {now}，"
                     "请用用户相同的语言回答问题。回答要简短精炼，不超过3句话。")
    conversation_history = [{"role": "system", "content": system_prompt}]

    use_voice_input  = mode in ("full", "voice+text")
    use_voice_output = mode in ("full", "text+voice")
    interrupted_input: str = ""
    last_ai_reply_for_echo_filter: str = ""
    tts_cooldown_until = 0.0
    request_times = deque()

    while True:
        if interrupted_input:
            user_input = interrupted_input
            interrupted_input = ""
        elif use_voice_input:
            user_input = ""
            consecutive_fails = 0
            MAX_VOICE_FAILS   = 10000
            try:
                while not user_input:
                    now_ts = time.time()
                    if now_ts < tts_cooldown_until:
                        wait_sec = max(0.0, tts_cooldown_until - now_ts)
                        print(f"[防自激] 播报后冷却中，{wait_sec:.1f}s 后恢复收音", flush=True)
                        time.sleep(min(0.5, wait_sec))
                        continue

                    user_input = listen_from_microphone()
                    if user_input and last_ai_reply_for_echo_filter:
                        similarity = _text_similarity(user_input, last_ai_reply_for_echo_filter)
                        if similarity >= SIMILARITY_BLOCK_THRESHOLD:
                            print(f"[防自激] 忽略疑似回声输入（相似度 {similarity:.2f}）", flush=True)
                            user_input = ""

                    if not user_input:
                        consecutive_fails += 1
                        if consecutive_fails >= MAX_VOICE_FAILS:
                            print(f"[语音] 连续 {MAX_VOICE_FAILS} 次无法录音/识别，"
                                  "切换为文字输入模式", flush=True)
                            try:
                                user_input = input("\n你(文字): ").strip()
                            except (KeyboardInterrupt, EOFError):
                                raise
                            break
                        if mode == "full":
                            print(f"[语音] 重试 ({consecutive_fails}/{MAX_VOICE_FAILS})...",
                                  flush=True)
                        else:
                            retry = input("未识别到语音，按 Enter 重试，或输入文字: ").strip()
                            if retry:
                                user_input = retry
                                break
                    else:
                        consecutive_fails = 0
            except (KeyboardInterrupt, EOFError):
                print("\n\n再见！")
                break
        else:
            try:
                user_input = input("\n你: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n\n再见！")
                break

        if not user_input:
            continue

        normalized = _normalize_command_text(user_input)
        if normalized in EXIT_EXACT_WORDS or any(w in normalized for w in EXIT_WORDS):
            print("再见！")
            if use_voice_output:
                speak("再见")
            break

        if "打开网易云音乐" in user_input or "打开网易云音乐" in normalized:
            ok, opened = _open_netease_cloudmusic()
            if ok:
                msg = "已为你打开网易云音乐。"
                print(msg)
                if use_voice_output:
                    speak(msg)
            else:
                msg = "未找到网易云音乐，请先确认已安装。"
                print(msg)
                if use_voice_output:
                    speak(msg)
            continue

        if "打开notepad" in normalized or "opennotepad" in normalized:
            ok, opened = _open_notepadpp()
            if ok:
                msg = "已为你打开 Notepad++。"
                print(msg)
                if use_voice_output:
                    speak(msg)
            else:
                msg = "未找到 Notepad++，请先确认已安装。"
                print(msg)
                if use_voice_output:
                    speak(msg)
            continue

        if user_input.lower() in ("clear", "清除"):
            conversation_history = [{"role": "system", "content": system_prompt}]
            print("对话历史已清除。")
            continue

        if user_input.lower() == "history":
            print("\n--- 对话历史 ---")
            for msg in conversation_history:
                if msg["role"] == "system":
                    continue
                role = "你" if msg["role"] == "user" else "AI"
                print(f"{role}: {msg['content']}")
            print("----------------")
            continue

        if user_input.lower() == "mode":
            mode = choose_mode()
            use_voice_input  = mode in ("full", "voice+text")
            use_voice_output = mode in ("full", "text+voice")
            print(f"已切换到：{mode_names.get(mode, mode)}")
            _announce_tts_status(mode)
            continue

        can_send, remaining = _can_send_request(request_times)
        if not can_send:
            print("[限流] 60秒内请求次数已达上限，请稍后再试。", flush=True)
            continue

        try:
            _log_step("AI回复开始")
            conversation_history, reply = chat(conversation_history, user_input)
            request_times.append(time.time())
            last_ai_reply_for_echo_filter = reply or ""
            if use_voice_output and reply:
                if use_voice_input:
                    interrupted_input = speak_interruptible(reply)
                    tts_cooldown_until = time.time() + TTS_INPUT_COOLDOWN_SECONDS
                else:
                    speak(reply)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()
