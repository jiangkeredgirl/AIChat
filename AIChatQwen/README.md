# Qwen-Audio 实时语音聊天

基于阿里云通义千问 Qwen-Audio Realtime API 的实时语音聊天软件。

## 功能

- 全双工实时语音对话（边听边说，支持打断）
- 支持 Plus（高质量）和 Flash（低延迟）两种模型
- 多种音色可选
- 实时显示对话文本（用户语音识别 + AI 回复）
- 服务端 VAD 自动检测语音起止

## 前置条件

1. **阿里云百炼账号**：前往 [百炼控制台](https://bailian.console.aliyun.com/) 开通
2. **获取 API Key**：在百炼控制台创建 API Key
3. **获取 Workspace ID**：在百炼控制台查看业务空间 ID

## 安装

```bash
cd E:\github\jiangke\AIChatQwen
pip install -r requirements.txt
```

> PyAudio 在 Windows 上可能需要额外安装：
> ```bash
> pip install pipwin
> pipwin install pyaudio
> ```

## 运行

### 方式一：直接运行（在界面中输入配置）

```bash
python main.py
```

启动后在界面中填入 API Key 和 Workspace ID，选择模型和音色，点击"连接"。

### 方式二：通过环境变量配置

```bash
set DASHSCOPE_API_KEY=your_api_key_here
set DASHSCOPE_WORKSPACE_ID=your_workspace_id_here
python main.py
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `main.py` | 主程序，tkinter GUI |
| `qwen_realtime.py` | Qwen-Audio Realtime WebSocket 客户端 |
| `audio_handler.py` | 麦克风采集和扬声器播放 |
| `config.py` | 配置（模型、音色、音频参数） |
| `requirements.txt` | Python 依赖 |

## 音频参数

| 方向 | 格式 | 采样率 | 位深 | 声道 |
|---|---|---|---|---|
| 输入（麦克风→API） | PCM | 16kHz | 16bit | 单声道 |
| 输出（API→扬声器） | PCM | 24kHz | 16bit | 单声道 |
