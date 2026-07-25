import os
import requests
from dotenv import load_dotenv

load_dotenv(override=False)

telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
telegram_url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage" if telegram_bot_token else None

def push(text: str) -> dict:
    safe_text = " ".join(str(text).split())[:3500]
    print(f"Push notification requested: {safe_text[:160]}", flush=True)
    if not telegram_bot_token or not telegram_chat_id or not telegram_url:
        print("Telegram bot is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file.")
        return {"ok": False, "message": "Contact notification is not configured."}

    payload = {"chat_id": str(telegram_chat_id), "text": safe_text}
    try:
        response = requests.post(telegram_url, data=payload, timeout=10)
        response.raise_for_status()
        print("Telegram notification sent", flush=True)
        return {"ok": True, "message": "Sathvik has been notified."}
    except requests.RequestException as exc:
        print(f"Telegram notification failed: {type(exc).__name__}", flush=True)
        return {"ok": False, "message": "The notification service is temporarily unavailable."}



def record_user_details(email: str, name: str = "Name not provided", notes: str = "Not provided"):
    email = " ".join(str(email).split())[:254]
    name = " ".join(str(name).split())[:120]
    notes = " ".join(str(notes).split())[:1000]
    return push(f"Portfolio lead — {name} | {email} | {notes}")


def record_unknown_question(question: str):
    question = " ".join(str(question).split())[:1000]
    return push(f"AI Twin could not answer: {question}")


record_user_details_json = {
    "name": "record_user_details",
    "description": "Use this tool to record that a user is interested in being in touch and provided an email address",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "The email address of this user"},
            "name": {"type": "string", "description": "The user's name, if they provided it"},
            "notes": {
                "type": "string",
                "description": "Any additional info about the conversation that's worth recording to give context",
            },
        },
        "required": ["email", "name", "notes"],
        "additionalProperties": False,
    },
    "strict": True,
}

record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": "Always use this tool to record any question that couldn't be answered as you didn't know the answer",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question that couldn't be answered"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
    "strict": True,
}

RESPONSE_TOOLS = [
    {"type": "function", **record_user_details_json},
    {"type": "function", **record_unknown_question_json},
]

TOOL_MAP = {
    "record_user_details": record_user_details,
    "record_unknown_question": record_unknown_question,
}

TOOL_STATUS = {
    "record_user_details": "Securely passing your details to Sathvik…",
    "record_unknown_question": "Flagging that question for Sathvik…",
}


def dispatch_tool(name: str, arguments: dict) -> dict:
    tool = TOOL_MAP.get(name)
    if tool is None:
        return {"ok": False, "message": "Unknown tool."}
    print(f"Tool called: {name}", flush=True)
    return tool(**arguments)


def tool_status(name: str) -> str:
    return TOOL_STATUS.get(name, "Checking that for you…")
