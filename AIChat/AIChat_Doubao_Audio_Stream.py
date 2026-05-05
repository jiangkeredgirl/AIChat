"""
AIChat_Doubao_Audio_Stream.py
豆包端到端实时语音聊天 + 文字聊天

模式1 文字聊天：
    键盘输入 → 豆包 LLM（流式）→ 终端输出

模式2 语音聊天（端到端实时语音大模型）：
    麦克风实时流 → 豆包 Realtime WebSocket
    ↕ 服务端 VAD 自动检测说话 / 打断
    豆包 Realtime WebSocket → 扬声器实时播放

功能：
    - 随时打断（说话即打断，服务端 VAD）
    - 回复不超过 3 句话
    - 支持询问当前时间
    - 说「退出/再见」、输入「退出」或按 Ctrl+C 退出
"""

import sys, io, os, asyncio, base64, json, queue
import struct, math, time, datetime, threading
import requests, pyaudio, websockets

# ── Windows UTF-8 ──────────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

# ════════════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════════════

API_KEY   = "ark-ce45b71c-48f6-4b17-bf2d-95b41405ffff-7b27f"
APP_ID    = "3"

# 文字聊天 REST 接口
CHAT_URL   = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
CHAT_MODEL = "doubao-seed-2-0-pro-260215"

# 实时语音 WebSocket 接口（端到端语音大模型）
REALTIME_URL   = "wss://ark.cn-beijing.volces.com/api/v3/realtime"
REALTIME_MODEL = "doubao-seed-1-6-realtime"    # 端到端实时语音大模型
REALTIME_VOICE = "BV001_streaming"             # 音色：女声；男声：BV700_streaming

# 音频参数
MIC_RATE  = 16000
MIC_CHUNK = 3200   # 200ms @ 16000Hz
OUT_RATE  = 24000  # 豆包 TTS 输出 24000Hz PCM
OUT_CHUNK = 4800
PA_FORMAT = pyaudio.paInt16

EXIT_WORDS = {"退出", "再见", "结束", "拜拜", "quit", "exit", "bye"}


def _system_prompt() -> str:
    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    return (
        f"你是一个智能语音助手，当前时间是 {now}。"
        "请用用户相同的语言回答问题，回答要简短精炼，不超过3句话。"
        "用户询问时间时，告知当前具体时间。"
    )


# ════════════════════════════════════════════════════════════════════════
# 工具：选择麦克风
# ════════════════════════════════════════════════════════════════════════

def _pick_mic() -> int | None:
    pa = pyaudio.PyAudio()
    inputs = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            name = info["name"]
            if sys.platform == "win32":
                for enc in ("latin-1", "utf-8"):
                    try:
                        name = name.encode(enc).decode("gbk"); break
                    except Exception:
                        pass
            inputs.append((i, name))
    pa.terminate()
    if not inputs:
        return None
    if len(inputs) == 1:
        print(f"[麦克风] {inputs[0][1]}")
        return inputs[0][0]
    pa2 = pyaudio.PyAudio()
    try:
        idx = pa2.get_default_input_device_info()["index"]
        pa2.terminate()
        name = next((n for i, n in inputs if i == idx), "")
        print(f"[麦克风] 默认: {name}")
        return idx
    except OSError:
        pa2.terminate()
    print("\n请选择麦克风：")
    for k, (di, n) in enumerate(inputs):
        print(f"  {k+1}. [{di}] {n}")
    while True:
        c = input(f"编号(1-{len(inputs)}): ").strip()
        if c.isdigit() and 1 <= int(c) <= len(inputs):
            sel = inputs[int(c)-1]; print(f"[麦克风] {sel[1]}"); return sel[0]


MIC_IDX = _pick_mic()


# ════════════════════════════════════════════════════════════════════════
# 模式1：文字聊天（流式 LLM）
# ════════════════════════════════════════════════════════════════════════

_history: list = []


def text_chat(user: str) -> str:
    """流式调用豆包 LLM，打印并返回完整回复。"""
    _history.append({"role": "user", "content": user})
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": _system_prompt()}] + _history,
        "temperature": 0.7,
        "stream": True,
    }
    print("\nAI: ", end="", flush=True)
    full = ""
    try:
        with requests.post(CHAT_URL, headers=headers, json=payload,
                           stream=True, timeout=30) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8").lstrip("data: ")
                if text == "[DONE]":
                    break
                try:
                    chunk = json.loads(text)
                    delta = chunk["choices"][0].get("delta", {})
                    c = delta.get("content", "")
                    if c:
                        print(c, end="", flush=True)
                        full += c
                except Exception:
                    pass
    except Exception as e:
        print(f"\n[错误] {e}")
    print()
    if full:
        _history.append({"role": "assistant", "content": full})
    return full


def run_text_mode():
    """文字聊天主循环。"""
    print("\n📝 文字聊天模式（输入「退出」或按 Ctrl+C 退出）\n")
    while True:
        try:
            user = input("你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！"); break
        if not user:
            continue
        if any(w in user for w in EXIT_WORDS):
            print("再见！"); break
        text_chat(user)


# ════════════════════════════════════════════════════════════════════════
# 模式2：实时语音聊天（端到端 Realtime WebSocket）
# ════════════════════════════════════════════════════════════════════════

class AudioPlayer:
    """PyAudio 输出流，支持随时打断（清空队列）。"""

    def __init__(self, rate: int = OUT_RATE):
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=PA_FORMAT, channels=1, rate=rate,
            output=True, frames_per_buffer=OUT_CHUNK,
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                pcm = self._q.get(timeout=0.05)
                self._stream.write(pcm)
            except queue.Empty:
                pass

    def play(self, pcm: bytes):
        self._q.put(pcm)

    def interrupt(self):
        """清空待播队列（打断当前播报）。"""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        self._stream.stop_stream()
        self._stream.close()
        self._pa.terminate()


async def _mic_sender(ws, stop_evt: asyncio.Event,
                      loop: asyncio.AbstractEventLoop):
    """从麦克风持续读取并通过 WebSocket 发送音频帧。"""
    if MIC_IDX is None:
        print("[语音] 无麦克风"); stop_evt.set(); return

    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=PA_FORMAT, channels=1, rate=MIC_RATE,
        input=True, input_device_index=MIC_IDX,
        frames_per_buffer=MIC_CHUNK,
    )

    def _read():
        return stream.read(MIC_CHUNK, exception_on_overflow=False)

    try:
        while not stop_evt.is_set():
            data = await loop.run_in_executor(None, _read)
            await ws.send(json.dumps({
                "type":  "input_audio_buffer.append",
                "audio": base64.b64encode(data).decode(),
            }))
    except Exception:
        pass
    finally:
        stream.stop_stream(); stream.close(); pa.terminate()


async def _ws_receiver(ws, player: AudioPlayer,
                       stop_evt: asyncio.Event, exit_flag: list):
    """接收 WebSocket 事件，驱动播放器或打印文字。"""
    try:
        async for raw in ws:
            if stop_evt.is_set():
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue

            t = ev.get("type", "")

            # 用户说话 → 打断正在播放的内容
            if t == "input_audio_buffer.speech_started":
                player.interrupt()
                print("\n[打断] ", end="", flush=True)

            # 音频输出片段 → 推入播放队列
            elif t == "response.audio.delta":
                pcm = base64.b64decode(ev.get("delta", ""))
                if pcm:
                    player.play(pcm)

            # 助手文字（流式）
            elif t == "response.audio_transcript.delta":
                print(ev.get("delta", ""), end="", flush=True)

            # 助手一轮回复结束
            elif t == "response.audio_transcript.done":
                text = ev.get("transcript", "")
                if text:
                    print(f"\nAI: {text}")
                    if any(w in text for w in EXIT_WORDS):
                        exit_flag[0] = True; stop_evt.set()

            # 用户语音转写完成
            elif t == "conversation.item.input_audio_transcription.completed":
                text = ev.get("transcript", "")
                if text:
                    print(f"\n你: {text}", flush=True)
                    if any(w in text for w in EXIT_WORDS):
                        exit_flag[0] = True; stop_evt.set()

            # 错误
            elif t == "error":
                msg = ev.get("error", {}).get("message", str(ev))
                print(f"\n[WS错误] {msg}")
                stop_evt.set()

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f"\n[接收异常] {e}")
    finally:
        stop_evt.set()


async def _voice_session() -> bool:
    """建立 Realtime WebSocket 会话，协调麦克风发送与音频接收。"""
    headers   = {"Authorization": f"Bearer {API_KEY}"}
    player    = AudioPlayer(rate=OUT_RATE)
    stop_evt  = asyncio.Event()
    exit_flag = [False]
    loop      = asyncio.get_event_loop()

    print("\n🎙  正在连接豆包实时语音大模型...", flush=True)
    try:
        async with websockets.connect(
            REALTIME_URL,
            additional_headers=headers,
            ping_interval=20,
            open_timeout=15,
        ) as ws:

            # 发送会话配置
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "model":        REALTIME_MODEL,
                    "instructions": _system_prompt(),
                    "voice":        REALTIME_VOICE,
                    "input_audio_format":  "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "doubao-asr"},
                    "turn_detection": {
                        "type":                "server_vad",
                        "threshold":           0.5,
                        "prefix_padding_ms":   300,
                        "silence_duration_ms": 800,
                    },
                },
            }))

            # 等待 session.created / session.updated 确认
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            ev  = json.loads(msg)
            if ev.get("type") == "error":
                raise RuntimeError(ev.get("error", {}).get("message", str(ev)))
            print(f"[连接成功] {ev.get('type')}  模型: "
                  f"{ev.get('session', {}).get('model', REALTIME_MODEL)}")
            print("🎤 请说话（停顿自动回复 · 说话可打断 · 说「退出」结束）\n")

            # 并发：麦克风发送 + WebSocket 接收
            await asyncio.gather(
                _mic_sender(ws, stop_evt, loop),
                _ws_receiver(ws, player, stop_evt, exit_flag),
            )

    except websockets.exceptions.WebSocketException as e:
        print(f"\n[WebSocket] 连接失败: {e}")
        print("请确认 REALTIME_MODEL 名称正确，并已在火山方舟控制台开通该模型。")
    except asyncio.TimeoutError:
        print("\n[超时] 连接超时，请检查网络")
    except RuntimeError as e:
        print(f"\n[API错误] {e}")
    except Exception as e:
        print(f"\n[异常] {e}")
    finally:
        player.close()

    return exit_flag[0]


def run_voice_mode():
    """语音聊天主循环。"""
    print("\n🎙  语音聊天模式 | 说「退出」或按 Ctrl+C 结束\n")
    try:
        asyncio.run(_voice_session())
    except KeyboardInterrupt:
        print("\n再见！")


# ════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("      豆包 AI 聊天助手")
    print("      端到端实时语音大模型 + 文字聊天")
    print("=" * 55)
    print("  1. 文字聊天")
    print("  2. 语音聊天（端到端实时语音大模型）")
    print("=" * 55)

    c = input("选择模式 (1/2，回车默认文字): ").strip()
    if c == "2":
        run_voice_mode()
    else:
        run_text_mode()


if __name__ == "__main__":
    main()
