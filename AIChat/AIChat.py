import os
from openai import OpenAI


def create_client():
    """创建 OpenAI 客户端"""
    api_key = os.environ.get("OPENAI_API_KEY", "your-api-key-here")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def chat(client, conversation_history, user_input, model="gpt-3.5-turbo"):
    """发送消息并获取回复（流式输出）"""
    conversation_history.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model=model,
        messages=conversation_history,
        stream=True,
    )

    print("\nAI: ", end="", flush=True)
    full_reply = ""
    for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)
            full_reply += delta.content
    print()

    conversation_history.append({"role": "assistant", "content": full_reply})
    return conversation_history


def main():
    print("=" * 50)
    print("        欢迎使用 AI 聊天助手")
    print("=" * 50)
    print("输入 'quit' 或 'exit' 退出程序")
    print("输入 'clear' 清除对话历史")
    print("输入 'history' 查看历史记录")
    print("=" * 50)

    client = create_client()
    model = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
    system_prompt = os.environ.get(
        "SYSTEM_PROMPT",
        "You are a helpful assistant. Please respond in the same language as the user.",
    )

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
            conversation_history = chat(client, conversation_history, user_input, model)
        except Exception as e:
            print(f"\n请求出错: {e}")


if __name__ == "__main__":
    main()
