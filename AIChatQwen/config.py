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
    "你是语音助手，必须严格遵守以下规则：\n"
    "1. 每次回答绝对不能超过3句话，最好只说1句。\n"
    "2. 每句话不超过15个字。\n"
    "3. 禁止使用列表、编号、解释、举例。\n"
    "4. 不要重复用户的问题，直接给答案。\n"
    "5. 像发微信短消息一样说话，能少说就少说。\n"
    "6. 如果用户没说话或只是语气词，简单回应即可，不要主动展开话题。"
)

# Auto mode settings
AUTO_DISCONNECT_TIMEOUT = 60  # seconds of no voice before auto-disconnect
VOICE_ENERGY_THRESHOLD = 500  # RMS threshold for voice detection (16-bit PCM)
