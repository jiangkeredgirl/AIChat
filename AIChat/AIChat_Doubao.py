import requests
import datetime

# ========== 填入你的豆包 API Key ==========
API_KEY = "ark-ce45b71c-48f6-4b17-bf2d-95b41405ffff-7b27f"
API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL = "doubao-seed-2-0-pro-260215"  # 从火山方舟控制台「推理接入点」复制，格式：ep-xxxxxxxxxxxxxxxx-xxxxx
# ==========================================

def get_system_prompt():
    now = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    return f"你是一个智能助手，当前时间是 {now}，请用用户相同的语言回答问题，回答要简短精炼，不超过3句话。"

chat_history = []

def chat(prompt):
    global chat_history
    chat_history.append({"role": "user", "content": prompt})
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    messages = [{"role": "system", "content": get_system_prompt()}] + chat_history
    data = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=data)
        resp.raise_for_status()
        result = resp.json()
        reply = result["choices"][0]["message"]["content"]
        chat_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"调用失败：{str(e)}"

if __name__ == "__main__":
    print("===== AI聊天机器人（输入 exit 退出） =====")
    while True:
        user = input("\n你：")
        if user.lower() in ["exit", "quit", "退出"]:
            print("对话结束")
            break
        ans = chat(user)
        print("\nAI：", ans)