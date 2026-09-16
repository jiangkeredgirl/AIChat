import os

# DashScope API Key (set via environment variable DASHSCOPE_API_KEY or paste here)
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# WebSocket URL (Beijing region)
# Replace {WorkspaceId} with your Bailian workspace ID
WORKSPACE_ID = os.environ.get("DASHSCOPE_WORKSPACE_ID", "")
WS_URL_TEMPLATE = "wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model={model}"

# Available models
MODELS = {
    "Plus (高质量)": "qwen-audio-3.0-realtime-plus",
    "Flash (低延迟)": "qwen-audio-3.0-realtime-flash",
}

# Available voices
VOICES = [
    "longanqian",
    "longanying",
    "longanrou",
    "longanjing",
    "longanxin",
    "longanxia",
    "longanming",
    "longanyue",
]

# Audio settings
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
FORMAT = "int16"
CHUNK_SIZE = 3200  # 100ms at 16kHz

# VAD settings
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 800

# Turn detection modes
TURN_DETECTION_MODES = {
    "Server VAD (自动)": "server_vad",
    "Smart Turn (智能判停)": "smart_turn",
    "Manual (手动)": "manual",
}
MANUAL_SILENCE_MS = 1200  # manual mode: silence duration before triggering response

# WebRTC VAD - filters non-human audio (noise, music, etc.)
# aggressiveness: 0=least aggressive, 3=most aggressive (filter more)
WEBRTC_VAD_ENABLED = True
WEBRTC_VAD_AGGRESSIVENESS = 3
VAD_MIN_SPEECH_MS = 500     # 连续检测到人声多久才确认"说话中"
VAD_MIN_SILENCE_MS = 400    # 连续检测到静音多久才确认"停止说话"

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
AUTO_DISCONNECT_TIMEOUT = 60  # seconds of no voice before auto-disconnect
VOICE_ENERGY_THRESHOLD = 500  # RMS threshold for voice detection (16-bit PCM)

# AI response auto-interrupt settings
# Mode: "timeout" = cancel after N seconds, "sentences" = cancel after N sentences
RESPONSE_INTERRUPT_MODE = "timeout"   # "timeout" or "sentences"
RESPONSE_TIMEOUT_SECONDS = 60         # used when mode is "timeout"
RESPONSE_MAX_SENTENCES = 10           # used when mode is "sentences"
