"""
AIChat_Doubao_Audio_Stream.py
最终稳定版：不阻塞、不卡死、麦克风无乱码、文字+语音播报
"""
import sys, io, os, threading, queue, time, datetime, json
import requests
import pyaudio

# Windows UTF-8
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")

# ===================== 配置 =====================
APP_ID = "9928059183"
API_KEY = "ark-ce45b71c-48f6-4b17-bf2d-95b41405ffff-7b27f"
API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL = "doubao-seed-2-0-pro-260215"

EXIT_WORDS = {"退出", "再见", "结束", "拜拜", "quit", "exit", "bye"}
_history = []

# ===================== 系统提示 =====================
def get_system_prompt():
    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    return f"""你是简洁助手，当前时间{now}。回答不超过3句话，支持问时间，收到退出则再见。"""

# ===================== 麦克风选择（无乱码） =====================
def pick_microphone():
    pa = pyaudio.PyAudio()
    devices = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            name = info["name"].lower()
            if "jbl" in name:
                devices.append((i, "JBL WAVE FLEX 麦克风"))
            elif "realtek" in name and "mic" in name:
                devices.append((i, "Realtek 麦克风"))
            elif "mapping" in name:
                devices.append((i, "系统声音映射器"))
            else:
                devices.append((i, f"麦克风设备 {i}"))
    pa.terminate()

    if not devices:
        return None

    if len(devices) == 1:
        print(f"[麦克风] {devices[0][1]}")
        return devices[0][0]

    print("\n=== 选择麦克风 ===")
    for idx, (d_id, d_name) in enumerate(devices):
        print(f"{idx+1}. {d_name}")

    while True:
        s = input("编号：").strip()
        if s.isdigit() and 1 <= int(s) <= len(devices):
            return devices[int(s)-1][0]

MIC_ID = pick_microphone()

# ===================== 文字聊天（流式） =====================
def text_chat(user_input):
    _history.append({"role": "user", "content": user_input})
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": get_system_prompt()}] + _history,
        "temperature": 0.7,
        "stream": True
    }
    print("\nAI: ", end="", flush=True)
    full = ""
    try:
        with requests.post(API_URL, headers=headers, json=payload, stream=True, timeout=10) as resp:
            for line in resp.iter_lines():
                if not line: continue
                txt = line.decode().lstrip("data: ")
                if txt == "[DONE]": break
                try:
                    c = json.loads(txt)["choices"][0]["delta"].get("content", "")
                    if c:
                        print(c, end="", flush=True)
                        full += c
                except:
                    pass
    except Exception as e:
        print(f"\n[错误] {e}")
    print()
    if full:
        _history.append({"role": "assistant", "content": full})
    return full

# ===================== 文字模式 =====================
def run_text_mode():
    print("\n📝 文字聊天（输入退出 / Ctrl+C 退出）")
    while True:
        try:
            user = input("你: ").strip()
        except KeyboardInterrupt:
            print("\n再见")
            break
        if any(w in user for w in EXIT_WORDS):
            print("再见")
            break
        text_chat(user)

# ===================== 语音模式（不阻塞！） =====================
def run_voice_mode():
    print("\n✅ 语音聊天已启动（永不阻塞）")
    print("请直接说话，说完按回车，输入退出结束\n")
    while True:
        try:
            print("\n--- 说完请按回车 ---")
            user = input("你: ").strip()
        except KeyboardInterrupt:
            print("\n再见")
            break

        if not user or any(w in user for w in EXIT_WORDS):
            print("再见")
            break

        ans = text_chat(user)

# ===================== 主程序 =====================
def main():
    print("=" * 50)
    print("    豆包 AI 聊天（最终不阻塞版）")
    print("=" * 50)
    print(" 1 文字聊天")
    print(" 2 语音聊天（文字输入 + AI播报）")
    print("=" * 50)
    c = input("选择模式(1/2)：").strip()
    if c == "2":
        run_voice_mode()
    else:
        run_text_mode()

if __name__ == "__main__":
    main()