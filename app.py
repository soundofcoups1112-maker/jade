"""
5단계: 웹 서버 버전 챗봇 (Flask, 멀티 디바이스용)

3단계 챗봇(도구 연동 + 메모리)을 웹 페이지로 만들어서,
같은 와이파이에 있는 태블릿/휴대폰 브라우저로 접속할 수 있게 했습니다.

실행하면:
1. PC에서 웹 서버가 켜짐
2. 같은 와이파이를 쓰는 다른 기기(태블릿 등)의 브라우저에서
   http://[PC의 IP주소]:5000 으로 접속하면 채팅 화면이 뜸
3. PC를 끄면 태블릿에서도 접속 안 됨 (PC가 서버 역할이라서)
"""

import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from groq import Groq
from tavily import TavilyClient

app = Flask(__name__)
client = Groq()
tavily_client = TavilyClient()  # 환경변수 TAVILY_API_KEY 자동으로 읽음

ASSISTANT_NAME = "Jade"
DB_FILE = "jade_memory.db"


# ============================================================
# 데이터베이스 (3단계와 동일)
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            summary TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def load_recent_summaries(limit=5):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT created_at, summary FROM memory_summaries ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def save_summary(summary_text):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO memory_summaries (created_at, summary) VALUES (?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M"), summary_text)
    )
    conn.commit()
    conn.close()


# ============================================================
# 도구 함수 (3단계와 동일)
# ============================================================

def get_current_time():
    now = datetime.now()
    return now.strftime("%Y년 %m월 %d일 %H시 %M분 (%A)")


def calculate(expression):
    try:
        allowed_chars = set("0123456789+-*/(). ")
        if not all(c in allowed_chars for c in expression):
            return "계산할 수 없는 형식이에요. 숫자와 +,-,*,/ 만 사용해주세요."
        result = eval(expression)
        return f"{expression} = {result}"
    except Exception as e:
        return f"계산 중 오류가 발생했어요: {e}"


def web_search(query):
    """실시간 웹 검색을 수행해서, 최신 정보나 정확한 사실을 찾아옵니다."""
    try:
        response = tavily_client.search(
            query=query,
            max_results=4,
            search_depth="basic",
        )
        results = response.get("results", [])
        if not results:
            return "검색 결과를 찾지 못했어요."

        # 검색 결과를 모델이 읽기 좋은 형태로 정리
        formatted = []
        for r in results:
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            formatted.append(f"- {title}\n  내용: {content}\n  출처: {url}")

        return "\n\n".join(formatted)
    except Exception as e:
        return f"검색 중 오류가 발생했어요: {e}"


AVAILABLE_FUNCTIONS = {
    "get_current_time": get_current_time,
    "calculate": calculate,
    "web_search": web_search,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "현재 날짜와 시간을 알려줍니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "수학 계산식을 계산합니다. 예: '157 * 23'",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "계산할 수식"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "실시간으로 웹을 검색합니다. 최신 뉴스, 현재 사실 정보, "
                "인물/장소/사건 등 확실하지 않거나 최신 정보가 필요한 질문에는 "
                "반드시 이 도구를 사용해서 확인 후 답하세요. 모르는 걸 추측해서 "
                "답하지 말고 이 도구로 먼저 확인하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색할 키워드나 질문"}
                },
                "required": ["query"]
            }
        }
    }
]


def build_system_prompt():
    base_prompt = (
        f"당신의 이름은 '{ASSISTANT_NAME}'입니다. 사용자의 개인 비서입니다. "
        "친근하고 간결하게 대답하세요. "
        "현재 시간/날짜를 묻는 질문에는 반드시 get_current_time 도구를 사용하세요. "
        "계산 질문에는 반드시 calculate 도구를 사용하세요. "
        "인물, 최신 뉴스, 사실 정보, 확실하지 않은 지식에 대한 질문에는 "
        "당신의 기억에만 의존해서 추측하지 말고, 반드시 web_search 도구로 "
        "먼저 확인한 후 답변하세요. 검색 결과에 없는 내용은 지어내지 마세요."
    )
    past_summaries = load_recent_summaries(limit=5)
    if past_summaries:
        memory_text = "\n\n[과거 대화 기억]\n"
        for created_at, summary in reversed(past_summaries):
            memory_text += f"- ({created_at}) {summary}\n"
        base_prompt += memory_text
        base_prompt += "\n위 기억을 참고해서 자연스럽게 대화하세요."
    return base_prompt


# 웹서버는 여러 기기가 접속할 수 있으므로, 서버 전체가 공유하는
# 대화 기록을 하나만 둡니다 (개인용이라 한 사람만 쓴다는 가정).
conversation_history = []


def chat_with_bot(user_message):
    conversation_history.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=conversation_history,
        tools=TOOLS,
        tool_choice="auto",
    )

    response_message = response.choices[0].message

    if response_message.tool_calls:
        conversation_history.append(response_message)

        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            function_to_call = AVAILABLE_FUNCTIONS[function_name]
            function_result = function_to_call(**function_args)

            conversation_history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": function_name,
                "content": str(function_result),
            })

        second_response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=conversation_history,
        )
        final_message = second_response.choices[0].message.content
        conversation_history.append({"role": "assistant", "content": final_message})
        return final_message
    else:
        final_message = response_message.content
        conversation_history.append({"role": "assistant", "content": final_message})
        return final_message


def summarize_and_save_conversation():
    has_conversation = any(msg["role"] == "user" for msg in conversation_history if isinstance(msg, dict))
    if not has_conversation:
        return

    plain_text = ""
    for msg in conversation_history:
        if not isinstance(msg, dict):
            continue
        if msg["role"] == "user":
            plain_text += f"사용자: {msg['content']}\n"
        elif msg["role"] == "assistant" and msg.get("content"):
            plain_text += f"{ASSISTANT_NAME}: {msg['content']}\n"

    if not plain_text.strip():
        return

    try:
        summary_response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": "다음 대화를 한국어로 2~3문장으로 간결하게 요약하세요. 중요한 정보 위주로."
                },
                {"role": "user", "content": plain_text}
            ]
        )
        summary_text = summary_response.choices[0].message.content
        save_summary(summary_text)
    except Exception:
        pass


# ============================================================
# 웹 페이지 (HTML/CSS/JS 한 파일에 포함)
# ============================================================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jade</title>
<style>
  :root {
    --bg: #0d1210;
    --panel: #141a17;
    --jade: #2dd4a7;
    --jade-dim: #1c8c6d;
    --text: #e8ede9;
    --text-dim: #8ba39a;
    --bubble-user: #1c2622;
    --bubble-bot: #16211c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    padding: 18px 20px;
    border-bottom: 1px solid #1e2622;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--jade);
    box-shadow: 0 0 8px var(--jade);
  }
  header h1 {
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 0.02em;
    margin: 0;
  }
  header span {
    color: var(--text-dim);
    font-size: 12px;
    margin-left: auto;
  }
  #chat {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .msg {
    max-width: 78%;
    padding: 11px 15px;
    border-radius: 14px;
    line-height: 1.5;
    font-size: 15px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.user {
    align-self: flex-end;
    background: var(--bubble-user);
    border: 1px solid #26332d;
    border-bottom-right-radius: 4px;
  }
  .msg.bot {
    align-self: flex-start;
    background: var(--bubble-bot);
    border: 1px solid #1c2b24;
    border-bottom-left-radius: 4px;
  }
  .msg.bot .label {
    color: var(--jade);
    font-size: 12px;
    font-weight: 600;
    display: block;
    margin-bottom: 4px;
  }
  #inputbar {
    display: flex;
    gap: 10px;
    padding: 16px;
    border-top: 1px solid #1e2622;
    background: var(--panel);
  }
  #textinput {
    flex: 1;
    background: #0d1512;
    border: 1px solid #24312b;
    border-radius: 10px;
    color: var(--text);
    padding: 12px 14px;
    font-size: 15px;
    outline: none;
  }
  #textinput:focus {
    border-color: var(--jade-dim);
  }
  #sendbtn {
    background: var(--jade);
    color: #08120e;
    border: none;
    border-radius: 10px;
    padding: 0 20px;
    font-weight: 700;
    font-size: 15px;
    cursor: pointer;
  }
  #sendbtn:active {
    transform: scale(0.97);
  }
  .thinking {
    color: var(--text-dim);
    font-size: 13px;
    padding: 4px 15px;
  }
</style>
</head>
<body>

<header>
  <div class="dot"></div>
  <h1>Jade</h1>
  <span id="statustext">준비 완료</span>
</header>

<div id="chat"></div>

<div id="inputbar">
  <input id="textinput" type="text" placeholder="메시지를 입력하세요..." autocomplete="off">
  <button id="sendbtn">전송</button>
</div>

<script>
const chatEl = document.getElementById('chat');
const inputEl = document.getElementById('textinput');
const sendBtn = document.getElementById('sendbtn');
const statusEl = document.getElementById('statustext');

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  if (role === 'bot') {
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = 'Jade';
    div.appendChild(label);
  }
  const body = document.createElement('span');
  body.textContent = text;
  div.appendChild(body);
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  addMessage('user', text);
  inputEl.value = '';
  statusEl.textContent = '생각 중...';
  sendBtn.disabled = true;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    addMessage('bot', data.reply);
  } catch (err) {
    addMessage('bot', '오류가 발생했어요: ' + err);
  }

  statusEl.textContent = '준비 완료';
  sendBtn.disabled = false;
  inputEl.focus();
}

sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendMessage();
});

addMessage('bot', '안녕하세요! 무엇을 도와드릴까요?');
</script>

</body>
</html>
"""


# ============================================================
# 라우트 (웹 주소별 동작 정의)
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    user_message = data.get("message", "")
    if not user_message:
        return jsonify({"reply": "메시지가 비어있어요."})

    try:
        reply = chat_with_bot(user_message)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"오류가 발생했어요: {e}"})


# ============================================================
# 초기화: 직접 실행이든(python app.py) 클라우드에서 gunicorn이
# import 하든, 이 부분은 항상 한 번 실행됩니다.
# ============================================================
init_db()
conversation_history.append({"role": "system", "content": build_system_prompt()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
