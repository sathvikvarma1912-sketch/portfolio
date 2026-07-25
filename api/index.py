"""Vercel-native FastAPI endpoint for Sathvik's portfolio AI twin."""

from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from twin.context import TWIN_SYSTEM_PROMPT
from twin.tools import RESPONSE_TOOLS, dispatch_tool, tool_status

load_dotenv(override=False)

app = FastAPI(
    title="Sathvik AI Twin",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

if not os.getenv("VERCEL"):
    # Let VS Code Live Server call the local API during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null"],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_HISTORY_MESSAGES = 14
MAX_TOTAL_HISTORY_CHARS = 24_000
MAX_TOOL_ROUNDS = 3
RATE_LIMIT_REQUESTS = 12
RATE_LIMIT_WINDOW_SECONDS = 60

_request_windows: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = Lock()
_client: OpenAI | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=24)


def _openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(timeout=45.0, max_retries=1)
    return _client


def _visitor_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "")
    agent = request.headers.get("user-agent", "")[:160]
    return sha256(f"{ip}|{agent}".encode("utf-8")).hexdigest()


def _enforce_rate_limit(visitor_key: str) -> None:
    now = monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        window = _request_windows[visitor_key]
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail="You are sending messages too quickly. Please wait a moment and try again.",
            )
        window.append(now)


def _bounded_history(messages: list[ChatMessage]) -> list[dict[str, str]]:
    if messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="The final message must be from the user.")

    bounded: list[dict[str, str]] = []
    char_count = 0
    for message in reversed(messages[-MAX_HISTORY_MESSAGES:]):
        content = message.content.strip()
        if not content:
            continue
        if bounded and char_count + len(content) > MAX_TOTAL_HISTORY_CHARS:
            break
        bounded.append({"role": message.role, "content": content})
        char_count += len(content)
    bounded.reverse()
    return bounded


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _response_stream(input_items: list[dict[str, str]], visitor_key: str):
    client = _openai_client()
    working_input: list = list(input_items)

    try:
        yield _sse("meta", {"model": MODEL_NAME})

        for tool_round in range(MAX_TOOL_ROUNDS):
            completed_response = None
            stream = client.responses.create(
                model=MODEL_NAME,
                instructions=TWIN_SYSTEM_PROMPT,
                input=working_input,
                tools=RESPONSE_TOOLS,
                stream=True,
                store=False,
                max_output_tokens=900,
                max_tool_calls=4,
                parallel_tool_calls=False,
                reasoning={"effort": "low"},
                text={"verbosity": "medium"},
                safety_identifier=f"portfolio-{visitor_key[:32]}",
            )

            for event in stream:
                if event.type == "response.output_text.delta":
                    yield _sse("delta", {"text": event.delta})
                elif event.type == "response.completed":
                    completed_response = event.response
                elif event.type == "response.failed":
                    message = getattr(getattr(event.response, "error", None), "message", None)
                    raise RuntimeError(message or "The model could not complete the response.")
                elif event.type == "error":
                    raise RuntimeError(getattr(event, "message", "OpenAI streaming error"))

            if completed_response is None:
                raise RuntimeError("The response stream ended before completion.")

            function_calls = [
                item for item in completed_response.output if item.type == "function_call"
            ]
            if not function_calls:
                yield _sse("done", {"ok": True})
                return

            if tool_round == MAX_TOOL_ROUNDS - 1:
                raise RuntimeError("The assistant reached its tool-call safety limit.")

            # Preserve all response items so call IDs and reasoning state remain intact.
            working_input += completed_response.output
            for call in function_calls:
                yield _sse("status", {"text": tool_status(call.name)})
                try:
                    arguments = json.loads(call.arguments)
                    result = dispatch_tool(call.name, arguments)
                except Exception:
                    result = {
                        "ok": False,
                        "message": "The requested action could not be completed.",
                    }
                working_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )

        raise RuntimeError("The assistant could not finish the request.")
    except Exception as exc:
        print(f"Chat stream failed: {type(exc).__name__}: {exc}", flush=True)
        yield _sse(
            "error",
            {
                "message": (
                    "I hit a temporary connection problem. Please try again in a moment."
                )
            },
        )


@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/", include_in_schema=False)
def local_portfolio():
    """Serve the static shell when running uvicorn locally; Vercel serves it directly."""
    return FileResponse(PROJECT_ROOT / "index.html")


@app.get("/model.webp", include_in_schema=False)
def local_model_image():
    return FileResponse(PROJECT_ROOT / "model.webp")


@app.post("/api/chat")
def chat(payload: ChatRequest, request: Request):
    visitor_key = _visitor_key(request)
    _enforce_rate_limit(visitor_key)
    messages = _bounded_history(payload.messages)
    return StreamingResponse(
        _response_stream(messages, visitor_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
