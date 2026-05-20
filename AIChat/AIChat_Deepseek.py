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
import requests


# ── DeepSeek 配置 ────────────────────────────────────────────────────
DEEPSEEK_API_KEY = "sk-892f8f3341d34354b8d245ade13d9269"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-v4-pro"

# ── 豆包 TTS（参考 doubao_tts_py-master HTTP）────────────────────────
DOUBAO_TTS_ENABLED = os.getenv("DOUBAO_TTS_ENABLED", "1") == "1"
DOUBAO_TTS_URL     = os.getenv("DOUBAO_TTS_URL", "https://openspeech.bytedance.com/api/v1/tts")
DOUBAO_TTS_APP_ID  = os.getenv("DOUBAO_TTS_APP_ID", "9928059183")
DOUBAO_TTS_TOKEN   = os.getenv("DOUBAO_TTS_TOKEN", "ni5KWLvTq2efj7JfJHfXO9iUv8hcOHVu")
DOUBAO_TTS_CLUSTER = os.getenv("DOUBAO_TTS_CLUSTER", "volcano_tts")
DOUBAO_TTS_VOICE   = os.getenv("DOUBAO_TTS_VOICE", "zh_female_meilinvyou_emo_v2_mars_bigtts")
DOUBAO_TTS_RATE    = int(os.getenv("DOUBAO_TTS_RATE", "24000"))
DOUBAO_TTS_ENCODING = "pcm"

DOUBAO_TTS_AVAILABLE = bool(DOUBAO_TTS_ENABLED and DOUBAO_TTS_APP_ID and DOUBAO_TTS_TOKEN)
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


# ── Whisper 离线语音识别模型（Google 在线识别失败时兜底）─────────────
# 模型选项: tiny / base / small / medium / large-v3（越大越准，占用越多）
WHISPER_MODEL_SIZE = "small"
EXIT_WORDS = {"退出", "再见", "结束", "拜拜", "quit", "exit", "bye"}
EXIT_EXACT_WORDS = {"quit", "exit", "退出"}
try:
    from faster_whisper import WhisperModel as _WhisperModel
    import numpy as _np
    print(f"[语音] 正在加载 faster-whisper {WHISPER_MODEL_SIZE} 模型（Google 失败时兜底）...", flush=True)
    _whisper_model = _WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    WHISPER_AVAILABLE = True
    print(f"[语音] faster-whisper {WHISPER_MODEL_SIZE} 加载完成，作为 Google 识别的离线兜底")
except ImportError:
    WHISPER_AVAILABLE = False
    _whisper_model = None
    print("[提示] 未安装 faster-whisper，Google 识别失败时将无离线兜底。"
          "可运行: pip install faster-whisper")
except Exception as e:
    WHISPER_AVAILABLE = False
    _whisper_model = None
    print(f"[提示] faster-whisper 加载失败: {e}，Google 识别失败时将无离线兜底。")


def _recognize(audio_data) -> str:
    """统一语音识别入口。
    优先使用 Google 在线识别（准确率高、支持多语言）；
    Google 不可用（网络异常、超时）时回退到本地 faster-whisper 离线识别。
    audio_data 可以是 sr.AudioData 或 bytes（int16 PCM，16000Hz）。
    """
    # ── Google 在线识别（优先）───────────────────────────────────────
    recognizer = sr.Recognizer()
    MAX_RETRIES = 3
    google_error = None
    # bytes/bytearray 需先包装为 AudioData
    if isinstance(audio_data, (bytes, bytearray)):
        audio_for_google = sr.AudioData(audio_data, 16000, 2)
    else:
        audio_for_google = audio_data
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            text = recognizer.recognize_google(audio_for_google, language="zh-CN")
            return text
        except sr.UnknownValueError:
            return ""   # 能连上但听不懂，不必重试
        except sr.RequestError as e:
            google_error = e
            if attempt < MAX_RETRIES:
                print(f"[语音] Google 识别出错（第{attempt}次，重试中）: {e}")
                time.sleep(1)
    print(f"[语音] Google 识别失败，切换到 faster-whisper 离线兜底: {google_error}")

    # ── faster-whisper 离线识别（兜底）──────────────────────────────
    if WHISPER_AVAILABLE and _whisper_model is not None:
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
            return text
        except Exception as e:
            print(f"[语音] faster-whisper 识别异常: {e}")
    else:
        print("[语音] faster-whisper 不可用，识别失败")
    return ""


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
                    cal.append(audio_q.get(timeout=0.2))
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
            print("🔍 识别中...", flush=True)
            audio_data = sr.AudioData(b"".join(frames), RATE, SAMPLE_WIDTH)
            try:
                text = _recognize(audio_data)
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
    try:
        resp = requests.post(DOUBAO_TTS_URL, headers=headers, json=body, timeout=20)
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
    if DOUBAO_TTS_AVAILABLE:
        print(f"[TTS] 使用: 豆包HTTP ({DOUBAO_TTS_VOICE})", flush=True)
        pcm = _tts_doubao_http(text)
        if pcm:
            try:
                _play_pcm(pcm, DOUBAO_TTS_RATE)
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


























def speak_interruptible(text: str) -> str:
    """可打断的语音播放。

    在语音输出的同时，通过 PyAudio 实时监测麦克风音量。
    用户开口说话（音量超过阈值）时，立刻终止 TTS 子进程，
    然后调用语音识别，将识别结果作为返回值返回。
    未被打断则返回空字符串。
    """
    if not VOICE_OUTPUT_AVAILABLE:
        return ""

    print("[TTS] 使用: pyttsx3（可打断）", flush=True)
    proc = subprocess.Popen([sys.executable, "-c", _build_tts_script(text)])
    interrupted = threading.Event()
    captured_audio = [None]   # 用列表传递跨线程的识别结果

    def _vad_and_capture():
        """VAD 监测 + 连续录音（回调模式，不阻塞）"""
        if not VOICE_INPUT_AVAILABLE:
            return
        try:
            import queue as _q, struct as _s, math as _m
            RATE          = _MIC_NATIVE_RATE
            CHUNK         = 1024
            SAMPLE_WIDTH  = 2
            VAD_THRESHOLD = 700
            CONFIRM_FRAMES = 3
            PRE_BUFFER_SEC = 1.0
            PAUSE_SEC      = 2.5

            pre_buf_max = int(RATE * PRE_BUFFER_SEC / CHUNK)
            pause_max   = int(RATE * PAUSE_SEC / CHUNK)

            audio_q = _q.Queue()

            def _cb(in_data, frame_count, time_info, status, _aq=audio_q):
                _aq.put(in_data)
                return (None, _pyaudio.paContinue)

            pa = _pyaudio.PyAudio()
            stream = pa.open(
                format=_pyaudio.paInt16, channels=1, rate=RATE,
                input=True, input_device_index=_MIC_DEVICE_INDEX,
                frames_per_buffer=CHUNK,
                stream_callback=_cb,
            )
            stream.start_stream()

            time.sleep(0.4)   # 防止扬声器声音触发 VAD

            rolling     = []
            consecutive = 0

            # ── 阶段1：等待用户开口（TTS 播放期间）──────────────────────
            while proc.poll() is None and not interrupted.is_set():
                try:
                    data = audio_q.get(timeout=0.1)
                except _q.Empty:
                    continue
                rolling.append(data)
                if len(rolling) > pre_buf_max:
                    rolling.pop(0)
                cnt    = len(data) // 2
                shorts = _s.unpack(f"{cnt}h", data)
                rms    = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms > VAD_THRESHOLD:
                    consecutive += 1
                    if consecutive >= CONFIRM_FRAMES:
                        interrupted.set()
                        break
                else:
                    consecutive = 0

            if not interrupted.is_set():
                stream.stop_stream(); stream.close(); pa.terminate()
                return

            # 终止 TTS 子进程
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()

            print("\n[打断] 正在聆听...", flush=True)

            # ── 阶段2：继续从回调队列录音，保留前1秒帧，直到静音 ────────
            speech_frames = list(rolling)
            silent        = 0
            max_frames    = int(RATE * 60 / CHUNK)

            while len(speech_frames) < max_frames:
                try:
                    data = audio_q.get(timeout=0.5)
                except _q.Empty:
                    break
                speech_frames.append(data)
                cnt    = len(data) // 2
                shorts = _s.unpack(f"{cnt}h", data)
                rms    = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms < VAD_THRESHOLD * 0.6:
                    silent += 1
                    if silent >= pause_max:
                        break
                else:
                    silent = 0

            stream.stop_stream(); stream.close(); pa.terminate()

            # ── 识别 ─────────────────────────────────────────────────
            raw        = b"".join(speech_frames)
            audio_data = sr.AudioData(raw, RATE, SAMPLE_WIDTH)
            print("🔍 识别中...", flush=True)
            try:
                text = _recognize(audio_data)
                if text:
                    print(f"你（语音）: {text}")
                    captured_audio[0] = text
                else:
                    print("[语音] 未能识别，请重新说话。")
                    captured_audio[0] = ""
            except Exception as e:
                print(f"[语音] 识别服务连接失败: {e}")
                captured_audio[0] = ""

        except Exception as e:
            print(f"[语音] VAD 异常: {e}")

    vad_thread = threading.Thread(target=_vad_and_capture, daemon=True)
    vad_thread.start()

    # 等待 TTS 播完或被打断
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()

    interrupted.set()          # 通知 VAD 线程退出（若 TTS 已自然结束）
    vad_thread.join(timeout=15)  # 等待识别完成

    return captured_audio[0] or ""


def speak_interruptible(text: str) -> str:
    """可打断的语音播放。

    在语音输出的同时，通过 PyAudio 实时监测麦克风音量。
    用户开口说话（音量超过阈值）时，立刻终止 TTS 子进程，
    然后调用语音识别，将识别结果作为返回值返回。
    未被打断则返回空字符串。
    """
    if not VOICE_OUTPUT_AVAILABLE:
        return ""

    proc = subprocess.Popen([sys.executable, "-c", _build_tts_script(text)])
    interrupted = threading.Event()
    captured_audio = [None]   # 用列表传递跨线程的识别结果

    def _vad_and_capture():
        """VAD 监测 + 连续录音（回调模式，不阻塞）"""
        if not VOICE_INPUT_AVAILABLE:
            return
        try:
            import queue as _q, struct as _s, math as _m
            RATE          = _MIC_NATIVE_RATE
            CHUNK         = 1024
            SAMPLE_WIDTH  = 2
            VAD_THRESHOLD = 700
            CONFIRM_FRAMES = 3
            PRE_BUFFER_SEC = 1.0
            PAUSE_SEC      = 2.5

            pre_buf_max = int(RATE * PRE_BUFFER_SEC / CHUNK)
            pause_max   = int(RATE * PAUSE_SEC / CHUNK)

            audio_q = _q.Queue()

            def _cb(in_data, frame_count, time_info, status, _aq=audio_q):
                _aq.put(in_data)
                return (None, _pyaudio.paContinue)

            pa = _pyaudio.PyAudio()
            stream = pa.open(
                format=_pyaudio.paInt16, channels=1, rate=RATE,
                input=True, input_device_index=_MIC_DEVICE_INDEX,
                frames_per_buffer=CHUNK,
                stream_callback=_cb,
            )
            stream.start_stream()

            time.sleep(0.4)   # 防止扬声器声音触发 VAD

            rolling     = []
            consecutive = 0

            # ── 阶段1：等待用户开口（TTS 播放期间）──────────────────────
            while proc.poll() is None and not interrupted.is_set():
                try:
                    data = audio_q.get(timeout=0.1)
                except _q.Empty:
                    continue
                rolling.append(data)
                if len(rolling) > pre_buf_max:
                    rolling.pop(0)
                cnt    = len(data) // 2
                shorts = _s.unpack(f"{cnt}h", data)
                rms    = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms > VAD_THRESHOLD:
                    consecutive += 1
                    if consecutive >= CONFIRM_FRAMES:
                        interrupted.set()
                        break
                else:
                    consecutive = 0

            if not interrupted.is_set():
                stream.stop_stream(); stream.close(); pa.terminate()
                return

            # 终止 TTS 子进程
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()

            print("\n[打断] 正在聆听...", flush=True)

            # ── 阶段2：继续从回调队列录音，保留前1秒帧，直到静音 ────────
            speech_frames = list(rolling)
            silent        = 0
            max_frames    = int(RATE * 60 / CHUNK)

            while len(speech_frames) < max_frames:
                try:
                    data = audio_q.get(timeout=0.5)
                except _q.Empty:
                    break
                speech_frames.append(data)
                cnt    = len(data) // 2
                shorts = _s.unpack(f"{cnt}h", data)
                rms    = _m.sqrt(sum(s * s for s in shorts) / cnt) if cnt else 0
                if rms < VAD_THRESHOLD * 0.6:
                    silent += 1
                    if silent >= pause_max:
                        break
                else:
                    silent = 0

            stream.stop_stream(); stream.close(); pa.terminate()

            # ── 识别 ─────────────────────────────────────────────────
            raw        = b"".join(speech_frames)
            audio_data = sr.AudioData(raw, RATE, SAMPLE_WIDTH)
            print("🔍 识别中...", flush=True)
            try:
                text = _recognize(audio_data)
                if text:
                    print(f"你（语音）: {text}")
                    captured_audio[0] = text
                else:
                    print("[语音] 未能识别，请重新说话。")
                    captured_audio[0] = ""
            except Exception as e:
                print(f"[语音] 识别服务连接失败: {e}")
                captured_audio[0] = ""

        except Exception as e:
            print(f"[语音] VAD 异常: {e}")

    vad_thread = threading.Thread(target=_vad_and_capture, daemon=True)
    vad_thread.start()

    # 等待 TTS 播完或被打断
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()

    interrupted.set()          # 通知 VAD 线程退出（若 TTS 已自然结束）
    vad_thread.join(timeout=15)  # 等待识别完成

    return captured_audio[0] or ""



# ── AI 聊天（流式） ───────────────────────────────────────────────────
def chat(conversation_history, user_input):
    """发送消息并流式获取 DeepSeek 回复"""
    conversation_history.append({"role": "user", "content": user_input})

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": conversation_history,
        "stream": True,
    }

    response = requests.post(DEEPSEEK_API_URL, headers=headers,
                             data=json.dumps(payload), stream=True)
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

    print("\nAI: ", end="", flush=True)
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
        # 网络中断（IncompleteRead、ChunkedEncodingError 等）
        # 已收到的内容保留，仅提示用户
        print(f"\n[提示] 网络传输中断: {e}，已收到部分回复。", flush=True)
    print()

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
    print(f"  [当前状态] 语音输入: {'\u2705 可用' if VOICE_INPUT_AVAILABLE else '\u274c 不可用'}  "
          f"语音播报: {'\u2705 可用' if VOICE_OUTPUT_AVAILABLE else '\u274c 不可用'}")
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

    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    system_prompt = (f"你是一个智能助手，当前时间是 {now}，"
                     "请用用户相同的语言回答问题。回答要简短精炼，不超过3句话。")
    conversation_history = [{"role": "system", "content": system_prompt}]

    use_voice_input  = mode in ("full", "voice+text")
    use_voice_output = mode in ("full", "text+voice")
    interrupted_input: str = ""   # 打断 TTS 时捕获的语音文字

    while True:
        # ── 获取用户输入 ──────────────────────────────────────────────
        if interrupted_input:
            # 上一轮 TTS 被打断，直接使用打断时识别到的文字
            user_input = interrupted_input
            interrupted_input = ""
        elif use_voice_input:
            user_input = ""
            consecutive_fails = 0
            MAX_VOICE_FAILS   = 10000
            try:
                while not user_input:
                    user_input = listen_from_microphone()
                    if not user_input:
                        consecutive_fails += 1
                        if consecutive_fails >= MAX_VOICE_FAILS:
                            print(f"[\u8bed\u97f3] \u8fde\u7eed {MAX_VOICE_FAILS} \u6b21\u65e0\u6cd5\u5f55\u97f3/\u8bc6\u522b\uff0c"
                                  "\u5207\u6362\u4e3a\u6587\u5b57\u8f93\u5165\u6a21\u5f0f", flush=True)
                            try:
                                user_input = input("\n\u4f60(\u6587\u5b57): ").strip()
                            except (KeyboardInterrupt, EOFError):
                                raise
                            break
                        if mode == "full":
                            print(f"[\u8bed\u97f3] \u91cd\u8bd5 ({consecutive_fails}/{MAX_VOICE_FAILS})...",
                                  flush=True)
                        else:
                            retry = input("\u672a\u8bc6\u522b\u5230\u8bed\u97f3\uff0c\u6309 Enter \u91cd\u8bd5\uff0c\u6216\u8f93\u5165\u6587\u5b57: ").strip()
                            if retry:
                                user_input = retry
                                break
                    else:
                        consecutive_fails = 0
            except (KeyboardInterrupt, EOFError):
                print("\n\n\u518d\u89c1\uff01")
                break
        else:
            try:
                user_input = input("\n你: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n\n再见！")
                break

        if not user_input:
            continue

        # ── 内置命令 ──────────────────────────────────────────────────
        normalized = user_input.lower()
        if normalized in EXIT_EXACT_WORDS or any(w in user_input for w in EXIT_WORDS):
            print("再见！")
            if use_voice_output:
                speak("再见")
            break

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

        # ── 调用 AI ───────────────────────────────────────────────────
        try:
            conversation_history, reply = chat(conversation_history, user_input)
            if use_voice_output and reply:
                if use_voice_input:
                    if DOUBAO_TTS_AVAILABLE:
                        print("[TTS] full模式: 走豆包HTTP播报（当前不支持打断）", flush=True)
                        speak(reply)
                        interrupted_input = ""
                    else:
                        # 语音输入模式：本地 pyttsx3 可打断，打断时捕获文字用于下一轮
                        interrupted_input = speak_interruptible(reply)
                else:
                    speak(reply)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()

