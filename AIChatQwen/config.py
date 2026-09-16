import os

# DashScope API Key (set via environment variable DASHSCOPE_API_KEY or paste here)
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# WebSocket URL (Beijing region)
# Replace {WorkspaceId} with your Bailian workspace ID
WORKSPACE_ID = os.environ.get("DASHSCOPE_WORKSPACE_ID", "")
WS_URL_TEMPLATE = "wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model={model}"

# Available models
MODELS = {
    "Qwen3.5-Omni Plus (多模态)": "qwen3.5-omni-plus-realtime",
    "Qwen3.5-Omni Flash (多模态)": "qwen3.5-omni-flash-realtime",
    "Qwen-Audio Plus (语音)": "qwen-audio-3.0-realtime-plus",
    "Qwen-Audio Flash (语音)": "qwen-audio-3.0-realtime-flash",
}

# Omni models support image/video input
OMNI_MODELS = {"qwen3.5-omni-plus-realtime", "qwen3.5-omni-flash-realtime"}

# Available voices
VOICES_AUDIO = [
    "longanqian", "longanying", "longanrou", "longanjing",
    "longanxin", "longanxia", "longanming", "longanyue",
]

VOICES_OMNI = [
    "Tina", "Ethan", "Cindy", "Raymond", "Serena", "Harvey", "Maia", "Evan",
    "Qiao", "Momo", "Wil", "Angel", "Mia", "Gold", "Katerina", "Ryan",
    "Jennifer", "Aiden", "Mione", "Sunny", "Dylan", "Eric", "Peter",
    "Kiki", "Rocky", "Sohee", "Lenn", "Chloe",
]

# Audio settings
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
FORMAT = "int16"
CHUNK_SIZE = 3200  # 100ms at 16kHz

# VAD settings
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 400  # 服务器检测到多久静音后触发回复（越小越快）

# Turn detection modes
TURN_DETECTION_MODES = {
    "Server VAD (自动)": "server_vad",
    "Smart Turn (智能判停)": "smart_turn",
    "Manual (手动)": "manual",
}
MANUAL_SILENCE_MS = 800  # manual mode: silence duration before triggering response

# WebRTC VAD - filters non-human audio (noise, music, etc.)
# aggressiveness: 0=least aggressive, 3=most aggressive (filter more)
WEBRTC_VAD_ENABLED = True
WEBRTC_VAD_AGGRESSIVENESS = 2   # 从3降到2，减少误过滤
VAD_ENERGY_THRESHOLD = 100      # 从200降到100，更灵敏
VAD_MIN_SPEECH_MS = 200         # 从300降到200，更快确认
VAD_MIN_SILENCE_MS = 200        # 从300降到200，更快确认

# System instructions - keep AI responses short
SYSTEM_INSTRUCTIONS = (
    "你是语音助手，请用自然口语回答问题，遵守以下规则：\n"
    "1. 每次回答2到5句话，每句话不超过100个字，把问题说清楚即可，不要啰嗦。\n"
    "2. 禁止使用列表、编号、markdown格式。\n"
    "3. 不要重复用户的问题，直接回答。\n"
    "4. 语气自然亲切，像朋友聊天。\n"
    "5. 如果用户没说话或只是语气词，简单回应即可。"
)

# Auto mode settings
AUTO_DISCONNECT_TIMEOUT = 180  # seconds of no voice before auto-disconnect
VOICE_ENERGY_THRESHOLD = 500  # RMS threshold for voice detection (16-bit PCM)

RESPONSE_COOLDOWN_SECONDS = 0.5  # AI回复结束后冷却期（阻止音频发送，防回声触发假回复）

# AI response auto-interrupt settings
# Mode: "timeout" = cancel after N seconds, "sentences" = cancel after N sentences
RESPONSE_INTERRUPT_MODE = "timeout"   # "timeout" or "sentences"
RESPONSE_TIMEOUT_SECONDS = 60         # used when mode is "timeout"
RESPONSE_MAX_SENTENCES = 10           # used when mode is "sentences"
