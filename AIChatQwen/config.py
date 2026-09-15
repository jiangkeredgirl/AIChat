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

# System instructions - keep AI responses short
SYSTEM_INSTRUCTIONS = (
    "你是一个语音助手。请用简短的口语回答问题，每次回答只说1到3句话，"
    "不要长篇大论。避免使用列表、编号、markdown格式。"
    "回答要自然、简洁，像朋友聊天一样。"
)

# Auto mode settings
AUTO_DISCONNECT_TIMEOUT = 60  # seconds of no voice before auto-disconnect
VOICE_ENERGY_THRESHOLD = 500  # RMS threshold for voice detection (16-bit PCM)
