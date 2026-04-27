import os
import json
import datetime
import requests

DEEPSEEK_API_KEY = "sk-892f8f3341d34354b8d245ade13d9269"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-reasoner"


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
    return conversation_history


def main():
    print("=" * 50)
    print("        欢迎使用 DeepSeek 聊天助手")
    print("=" * 50)
    print("输入 'quit' 或 'exit' 退出程序")
    print("输入 'clear' 清除对话历史")
    print("输入 'history' 查看历史记录")
    print("=" * 50)

    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    system_prompt = f"你是一个智能助手，当前时间是 {now}，请用用户相同的语言回答问题。回答要简短精炼，不超过3句话。"
    conversation_history = [{"role": "system", "content": system_prompt}]

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("再见！")
            break

        if user_input.lower() == "clear":
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

        try:
            conversation_history = chat(conversation_history, user_input)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()
