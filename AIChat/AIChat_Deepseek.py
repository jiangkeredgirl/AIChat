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
import requests

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
        """自动或交互选择麦克风设备索引，无可用设备返回 None"""
        devices = _list_microphones()
        if not devices:
            return None
        # 只有一个设备直接使用
        if len(devices) == 1:
            print(f"[语音] 使用麦克风: {devices[0][1]}")
            return devices[0][0]
        # 多个设备尝试获取系统默认
        pa = _pyaudio.PyAudio()
        default_idx = None
        try:
            default_idx = pa.get_default_input_device_info()["index"]
        except OSError:
            pass
        pa.terminate()
        if default_idx is not None:
            name = next((n for i, n in devices if i == default_idx), "")
            print(f"[语音] 使用默认麦克风: {name}")
            return default_idx
        # 无默认设备则让用户选择
        print("\n检测到多个麦克风设备，请选择：")
        for idx, (dev_i, name) in enumerate(devices):
            print(f"  {idx + 1}. [{dev_i}] {name}")
        while True:
            choice = input(f"输入编号（1-{len(devices)}）: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(devices):
                selected = devices[int(choice) - 1]
                print(f"[语音] 已选择: {selected[1]}")
                return selected[0]
            print("输入无效，请重新输入。")

    _MIC_DEVICE_INDEX = _select_microphone_index()
    VOICE_INPUT_AVAILABLE = _MIC_DEVICE_INDEX is not None
    if not VOICE_INPUT_AVAILABLE:
        print("[提示] 未检测到可用麦克风设备，语音输入不可用。")
except ImportError:
    VOICE_INPUT_AVAILABLE = False
    _MIC_DEVICE_INDEX = None
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

# ── DeepSeek 配置 ────────────────────────────────────────────────────
DEEPSEEK_API_KEY = "sk-892f8f3341d34354b8d245ade13d9269"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-reasoner"


# ── 语音输入 ─────────────────────────────────────────────────────────
def listen_from_microphone() -> str:
    """从麦克风录音并识别为文字，失败返回空字符串"""
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 2.0       # 停顿超过2秒才结束，避免自然停顿被截断
    recognizer.non_speaking_duration = 0.8 # 开始录音前的静音容忍
    recognizer.energy_threshold = 300      # 录音启动灵敏度
    try:
        mic = sr.Microphone(device_index=_MIC_DEVICE_INDEX)
    except Exception as e:
        print(f"[语音] 无法创建麦克风对象: {e}")
        return ""
    try:
        with mic as source:
            print("🎤 正在聆听（说完后停顿2秒结束）...", flush=True)
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=30)
            except sr.WaitTimeoutError:
                print("[语音] 未检测到声音，请重试。")
                return ""
    except KeyboardInterrupt:
        raise
    except AssertionError:
        print("[语音] 麦克风音频流未就绪，请检查设备是否被其他程序占用。")
        return ""
    except Exception as e:
        print(f"[语音] 麦克风读取失败: {e}")
        return ""
    print("🔍 识别中...", flush=True)
    try:
        text = recognizer.recognize_google(audio, language="zh-CN")
        print(f"你（语音）: {text}")
        return text
    except sr.UnknownValueError:
        print("[语音] 未能识别，请重新说话。")
        return ""
    except sr.RequestError as e:
        print(f"[语音] 识别服务出错: {e}")
        return ""


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
    if not VOICE_OUTPUT_AVAILABLE:
        return
    try:
        subprocess.run(
            [sys.executable, "-c", _build_tts_script(text)],
            timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        print(f"[语音] 朗读失败: {e}")


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
        """麦克风 VAD 监测：检测到说话 → 终止 TTS → 录音识别"""
        if not VOICE_INPUT_AVAILABLE:
            return
        try:
            import pyaudio, struct, math
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=_MIC_DEVICE_INDEX,
                frames_per_buffer=1024,
            )
            # TTS 刚开始的短暂延迟，防止扬声器声音反馈触发
            time.sleep(0.4)

            VAD_THRESHOLD    = 700   # RMS 阈值，可按环境调整
            CONFIRM_FRAMES   = 3     # 连续 N 帧超阈值才确认为说话
            consecutive = 0

            while proc.poll() is None and not interrupted.is_set():
                data = stream.read(1024, exception_on_overflow=False)
                count = len(data) // 2
                shorts = struct.unpack(f"{count}h", data)
                rms = math.sqrt(sum(s * s for s in shorts) / count) if count else 0
                if rms > VAD_THRESHOLD:
                    consecutive += 1
                    if consecutive >= CONFIRM_FRAMES:
                        interrupted.set()
                        break
                else:
                    consecutive = 0

            stream.stop_stream()
            stream.close()
            pa.terminate()

            if interrupted.is_set() and proc.poll() is None:
                # 终止 TTS
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                print("\n[打断] 请继续说...", flush=True)
                # 必须先彻底关闭 VAD 的 PyAudio 流，再开新流录音
                # 否则同一设备两个流并存会导致录音被截断
                time.sleep(0.15)   # 等待设备释放
                captured_audio[0] = listen_from_microphone()
        except Exception:
            pass

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
            try:
                while not user_input:
                    user_input = listen_from_microphone()
                    if not user_input:
                        retry = input("未识别到语音，按 Enter 重试，或输入文字: ").strip()
                        if retry:
                            user_input = retry
                            break
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

        # ── 内置命令 ──────────────────────────────────────────────────
        if user_input.lower() in ("quit", "exit", "退出"):
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
            continue

        # ── 调用 AI ───────────────────────────────────────────────────
        try:
            conversation_history, reply = chat(conversation_history, user_input)
            if use_voice_output and reply:
                if use_voice_input:
                    # 语音输入模式：支持打断，打断时捕获的文字直接用于下一轮
                    interrupted_input = speak_interruptible(reply)
                else:
                    speak(reply)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()

