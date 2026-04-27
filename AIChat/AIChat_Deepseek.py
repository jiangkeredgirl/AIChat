import os
import json
import datetime
import requests

# ── 语音依赖（可选，未安装时自动降级为文字模式）──────────────────────────
try:
    import speech_recognition as sr
    import pyaudio as _pyaudio

    def _list_microphones():
        """返回 [(index, name), ...] 的可用麦克风列表"""
        pa = _pyaudio.PyAudio()
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devices.append((i, info["name"]))
        pa.terminate()
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
    # 优先选择中文语音
    for voice in _tts_engine.getProperty("voices"):
        if "chinese" in voice.name.lower() or "zh" in voice.id.lower():
            _tts_engine.setProperty("voice", voice.id)
            break
    _tts_engine.setProperty("rate", 180)   # 语速
    _tts_engine.setProperty("volume", 1.0) # 音量
    VOICE_OUTPUT_AVAILABLE = True
except Exception:
    VOICE_OUTPUT_AVAILABLE = False
    print("[提示] 未安装 pyttsx3，语音输出不可用。"
          "可运行: pip install pyttsx3")

# ── DeepSeek 配置 ────────────────────────────────────────────────────
DEEPSEEK_API_KEY = "sk-892f8f3341d34354b8d245ade13d9269"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-reasoner"


# ── 语音输入 ─────────────────────────────────────────────────────────
def listen_from_microphone() -> str:
    """从麦克风录音并识别为文字，失败返回空字符串"""
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 1.0  # 停顿超过1秒即结束
    try:
        mic = sr.Microphone(device_index=_MIC_DEVICE_INDEX)
    except OSError as e:
        print(f"[语音] 无法打开麦克风设备: {e}")
        return ""
    try:
        with mic as source:
            print("🎤 正在聆听（说话后停顿即结束）...", flush=True)
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=20)
            except sr.WaitTimeoutError:
                print("[语音] 未检测到声音，请重试。")
                return ""
    except OSError as e:
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
def speak(text: str):
    """将文字朗读出来"""
    if not VOICE_OUTPUT_AVAILABLE:
        return
    try:
        _tts_engine.say(text)
        _tts_engine.runAndWait()
    except Exception as e:
        print(f"[语音] 朗读出错: {e}")


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
    print()

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

    while True:
        # ── 获取用户输入 ──────────────────────────────────────────────
        if use_voice_input:
            user_input = ""
            while not user_input:
                user_input = listen_from_microphone()
                if not user_input:
                    retry = input("未识别到语音，按 Enter 重试，或输入文字: ").strip()
                    if retry:
                        user_input = retry
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
                speak(reply)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()

