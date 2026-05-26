"""
chat.deepseek.com → Anthropic API Proxy
========================================
Exposes a local HTTP server that speaks the Anthropic Messages API,
but routes every request through chat.deepseek.com via Playwright.

Usage:
    pip install playwright flask rich
    playwright install chromium
    python deepseek_server_fixed.py

Then in another shell:
    export ANTHROPIC_BASE_URL="http://localhost:8765"
    export ANTHROPIC_API_KEY="local-proxy-key"
    claude   # or any tool that uses the Anthropic SDK

Supported endpoints:
    POST /v1/messages   (streaming + non-streaming)
    GET  /v1/models
    GET  /health

Claude Code compatibility fixes applied:
  - Reasoning blocks returned as a separate top-level `thinking` content block
    (type="thinking") so Claude Code ignores them cleanly, not injected into text
  - Streaming: proper SSE framing with correct anthropic-beta header support
  - Token counts use character-based estimation (closer to real BPE)
  - system prompt deduplication guard
  - anthropic-version header no longer required (relaxed auth)
"""

import json
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, request, Response, jsonify

# ── reuse the scraper (must be in same folder) ────────────────
from deepseek_scraper import DeepSeekScraper

# ─────────────────────────────────────────────────────────────
# Global scraper instance (one browser, one session)
# ─────────────────────────────────────────────────────────────

_scraper: DeepSeekScraper | None = None
_scraper_lock   = threading.Lock()
_headless_flag  = False
_search_flag    = False
_deepthink_flag = False
_expert_flag    = False


def get_scraper() -> DeepSeekScraper:
    global _scraper
    with _scraper_lock:
        if _scraper is None:
            _scraper = DeepSeekScraper(
                headless=_headless_flag,
                enable_search=_search_flag,
                enable_deepthink=_deepthink_flag,
                enable_expert=_expert_flag,
            )
            _scraper.start()
        return _scraper


# ─────────────────────────────────────────────────────────────
# Token estimation
# ─────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough BPE estimate: ~4 chars per token, minimum 1."""
    return max(1, len(text) // 4)


# ─────────────────────────────────────────────────────────────
# Message Formatting Helpers
# ─────────────────────────────────────────────────────────────

def _content_to_text(content) -> str:
    """
    Normalise the `content` field of a single message into a plain string.
    Handles: str, list of content blocks (text / tool_result / tool_use / image).
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, list):
                    for rb in inner:
                        if isinstance(rb, dict) and rb.get("type") == "text":
                            parts.append(f"[Tool Result]\n{rb.get('text', '')}")
                else:
                    parts.append(f"[Tool Result]\n{inner}")
            elif btype == "tool_use":
                name = block.get("name", "tool")
                inp  = json.dumps(block.get("input", {}), indent=2)
                parts.append(f"[Tool Call: {name}]\n{inp}")
            # skip image blocks — can't forward to DeepSeek
        return "\n".join(parts)

    return str(content)


def _messages_to_prompt(messages: list) -> str:
    """
    Flatten an Anthropic `messages` array into a plain-text prompt.
    system entries are already prepended by _extract_prompt — do NOT add them again.
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        if role == "system":
            # guard: should not reach here, but handle gracefully
            parts.append(f"[System Instructions]\n{text}")
        elif role == "user":
            parts.append(f"Human: {text}")
        elif role == "assistant":
            parts.append(f"Assistant: {text}")
        else:
            parts.append(text)
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _extract_prompt(body: dict) -> str:
    """
    Build the final prompt string to send to DeepSeek.

    Rules:
      • system is the TOP-LEVEL "system" field in the Anthropic request body,
        NOT a message with role="system" in the messages array. Treat them the
        same but deduplicate so the system text only appears once.
      • Single user turn, no system → send raw user text (cleanest input).
      • Otherwise → flatten into Human:/Assistant: dialogue with system header.
    """
    messages = body.get("messages", [])

    # Collect system text from top-level field (Anthropic convention)
    system_text = ""
    raw_system = body.get("system", "")
    if isinstance(raw_system, str):
        system_text = raw_system.strip()
    elif isinstance(raw_system, list):
        # system can be a list of content blocks in newer API versions
        system_text = _content_to_text(raw_system).strip()

    # Filter out any role="system" messages from the messages array
    # (some SDKs inject them there; deduplicate against top-level system)
    filtered_messages = []
    for m in messages:
        if m.get("role") == "system":
            extra = _content_to_text(m.get("content", "")).strip()
            # Only append if it's different from the top-level system text
            if extra and extra not in system_text:
                system_text = (system_text + "\n\n" + extra).strip() if system_text else extra
        else:
            filtered_messages.append(m)

    user_msgs = [m for m in filtered_messages if m.get("role") == "user"]

    # Simple case: single user turn, no system → raw text
    if len(user_msgs) == 1 and len(filtered_messages) == 1 and not system_text:
        return _content_to_text(user_msgs[0].get("content", ""))

    # Multi-turn or system present → structured dialogue
    all_messages = []
    if system_text:
        all_messages.append({"role": "system", "content": system_text})
    all_messages.extend(filtered_messages)
    return _messages_to_prompt(all_messages)


# ─────────────────────────────────────────────────────────────
# Response Builders
# ─────────────────────────────────────────────────────────────

def _build_content_blocks(response_md: str, reasoning_blocks: list) -> list:
    """
    Build the `content` array for an Anthropic response.

    Claude Code (and all Anthropic SDK consumers) expect:
      - Optional thinking blocks FIRST  (type="thinking", thinking=<text>)
      - Then the main text block        (type="text",     text=<markdown>)

    Injecting reasoning into the text body (the old approach) breaks Claude
    Code because it tries to parse the text as continuation of a tool-call
    or command output and chokes on the Markdown decoration.
    """
    blocks = []

    # Reasoning blocks as proper "thinking" type — Claude Code skips these
    for rb in reasoning_blocks:
        if rb.strip():
            blocks.append({
                "type":      "thinking",
                "thinking":  rb,
            })

    # Main response — clean, no decoration
    blocks.append({
        "type": "text",
        "text": response_md,
    })

    return blocks


def _build_response_body(
    response_md: str,
    reasoning_blocks: list,
    model: str,
    input_tokens: int = 0,
) -> dict:
    """Build a valid non-streaming Anthropic /v1/messages response."""
    content = _build_content_blocks(response_md, reasoning_blocks)
    output_tokens = _estimate_tokens(response_md)
    for rb in reasoning_blocks:
        output_tokens += _estimate_tokens(rb)

    return {
        "id":            f"msg_{uuid.uuid4().hex[:24]}",
        "type":          "message",
        "role":          "assistant",
        "content":       content,
        "model":         model,
        "stop_reason":   "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens":  max(input_tokens, 1),
            "output_tokens": max(output_tokens, 1),
        },
    }


# ─────────────────────────────────────────────────────────────
# SSE Streaming
# ─────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _stream_response(response_md: str, reasoning_blocks: list, model: str):
    """
    Yield SSE events matching the Anthropic streaming wire format.

    Block order:
      [thinking block(s)] → [text block]

    This is critical for Claude Code: it uses the content_block_start `type`
    field to decide how to handle each block. Reasoning in a "thinking" block
    is silently consumed; reasoning injected into a "text" block breaks parsing.
    """
    msg_id        = f"msg_{uuid.uuid4().hex[:24]}"
    chunk_size    = 64          # larger chunks = fewer events = better perf
    output_tokens = _estimate_tokens(response_md)
    for rb in reasoning_blocks:
        output_tokens += _estimate_tokens(rb)

    # ── message_start ─────────────────────────────────────────
    yield _sse({
        "type": "message_start",
        "message": {
            "id":            msg_id,
            "type":          "message",
            "role":          "assistant",
            "content":       [],
            "model":         model,
            "stop_reason":   None,
            "stop_sequence": None,
            "usage":         {"input_tokens": 1, "output_tokens": 1},
        },
    })

    block_index = 0

    # ── thinking blocks (one per reasoning step) ──────────────
    for rb in reasoning_blocks:
        if not rb.strip():
            continue

        yield _sse({
            "type":          "content_block_start",
            "index":         block_index,
            "content_block": {"type": "thinking", "thinking": ""},
        })

        for i in range(0, len(rb), chunk_size):
            yield _sse({
                "type":  "content_block_delta",
                "index": block_index,
                "delta": {"type": "thinking_delta", "thinking": rb[i: i + chunk_size]},
            })

        yield _sse({"type": "content_block_stop", "index": block_index})
        block_index += 1

    # ── main text block ───────────────────────────────────────
    yield _sse({
        "type":          "content_block_start",
        "index":         block_index,
        "content_block": {"type": "text", "text": ""},
    })

    for i in range(0, len(response_md), chunk_size):
        yield _sse({
            "type":  "content_block_delta",
            "index": block_index,
            "delta": {"type": "text_delta", "text": response_md[i: i + chunk_size]},
        })

    yield _sse({"type": "content_block_stop", "index": block_index})

    # ── message_delta + message_stop ──────────────────────────
    yield _sse({
        "type":  "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": max(output_tokens, 1)},
    })

    yield _sse({"type": "message_stop"})
    # The Anthropic SDK expects [DONE] as the final SSE event
    yield "data: [DONE]\n\n"


# ─────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────

app = Flask(__name__)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": (
        "Content-Type, Authorization, x-api-key, "
        "anthropic-version, anthropic-beta"
    ),
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


@app.after_request
def _add_cors(response):
    for k, v in _CORS_HEADERS.items():
        response.headers[k] = v
    return response


def _cors_preflight():
    resp = Response("", status=204)
    for k, v in _CORS_HEADERS.items():
        resp.headers[k] = v
    return resp


# ── POST /v1/messages ─────────────────────────────────────────

@app.route("/v1/messages", methods=["POST", "OPTIONS"])
def messages():
    if request.method == "OPTIONS":
        return _cors_preflight()

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": {
            "type":    "invalid_request_error",
            "message": "Invalid JSON body",
        }}), 400

    model  = body.get("model", "deepseek-chat")
    stream = body.get("stream", False)

    if not body.get("messages"):
        return jsonify({"error": {
            "type":    "invalid_request_error",
            "message": "messages field is required",
        }}), 400

    prompt = _extract_prompt(body).strip()
    if not prompt:
        return jsonify({"error": {
            "type":    "invalid_request_error",
            "message": "Prompt is empty after extraction",
        }}), 400

    # ── Send to DeepSeek ──────────────────────────────────────
    try:
        scraper = get_scraper()
        response_md, reasoning_blocks, _elapsed = scraper.send_message(prompt)
    except Exception as e:
        return jsonify({"error": {
            "type":    "api_error",
            "message": f"DeepSeek scraper error: {e}",
        }}), 500

    if response_md.startswith("[Error]"):
        return jsonify({"error": {
            "type":    "api_error",
            "message": response_md,
        }}), 500

    input_tokens = _estimate_tokens(prompt)

    # ── Stream or return ──────────────────────────────────────
    if stream:
        return Response(
            _stream_response(response_md, reasoning_blocks, model),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return jsonify(
        _build_response_body(response_md, reasoning_blocks, model, input_tokens)
    )


# ── GET /v1/models ────────────────────────────────────────────

@app.route("/v1/models", methods=["GET", "OPTIONS"])
def list_models():
    if request.method == "OPTIONS":
        return _cors_preflight()

    now = 1720000000
    return jsonify({
        "data": [
            {"id": "deepseek-chat",      "object": "model", "created": now, "owned_by": "deepseek"},
            {"id": "deepseek-reasoner",  "object": "model", "created": now, "owned_by": "deepseek"},
            # Anthropic aliases so tools that hard-code Claude model names still work
            {"id": "claude-opus-4-5",    "object": "model", "created": now, "owned_by": "anthropic"},
            {"id": "claude-sonnet-4-5",  "object": "model", "created": now, "owned_by": "anthropic"},
            {"id": "claude-haiku-3-5",   "object": "model", "created": now, "owned_by": "anthropic"},
            {"id": "claude-opus-4-6",    "object": "model", "created": now, "owned_by": "anthropic"},
            {"id": "claude-sonnet-4-6",  "object": "model", "created": now, "owned_by": "anthropic"},
            {"id": "claude-haiku-4-5",   "object": "model", "created": now, "owned_by": "anthropic"},
        ]
    })


# ── GET /health ───────────────────────────────────────────────

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "proxy":   "chat.deepseek.com → Anthropic API",
        "browser": "ready" if _scraper is not None else "not started",
        "time":    datetime.now().isoformat(),
    })


# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="chat.deepseek.com → Anthropic API Proxy")
    ap.add_argument("--host",      default="0.0.0.0",      help="Bind host (default: 0.0.0.0)")
    ap.add_argument("--port",      default=8765, type=int,  help="Port (default: 8765)")
    ap.add_argument("--headless",  action="store_true",     help="Run Chromium headless")
    ap.add_argument("--search",    action="store_true",     help="Enable Search toggle")
    ap.add_argument("--deepthink", action="store_true",     help="Enable DeepThink toggle")
    ap.add_argument("--expert",    action="store_true",     help="Enable Expert model")
    ap.add_argument("--no-warmup", action="store_true",     help="Lazy-init browser")
    args = ap.parse_args()

    _headless_flag  = args.headless
    _search_flag    = args.search
    _deepthink_flag = args.deepthink
    _expert_flag    = args.expert

    banner = f"""
{'=' * 60}
   chat.deepseek.com  →  Anthropic API Proxy
   Claude Code compatible edition
{'=' * 60}
  Listening on : http://{args.host}:{args.port}
  Headless     : {args.headless}
  Search       : {args.search}
  DeepThink    : {args.deepthink}
  Expert model : {args.expert}

  Configure your tool:
    export ANTHROPIC_BASE_URL="http://localhost:{args.port}"
    export ANTHROPIC_API_KEY="local-proxy-key"

  Endpoints:
    POST /v1/messages   (streaming + non-streaming)
    GET  /v1/models
    GET  /health
{'=' * 60}
"""
    print(banner)

    if not args.no_warmup:
        print("[*] Pre-launching browser (pass --no-warmup to skip) ...")
        get_scraper()
        print("[+] Browser ready. Proxy is live!\n")

    app.run(host=args.host, port=args.port, threaded=False, debug=False)
