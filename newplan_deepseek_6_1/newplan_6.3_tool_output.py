#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║              HACKERS AI — Advanced Linux Agent               ║
║         General Purpose + Authorized Pentesting Suite        ║
║                   Single-File Architecture v7.1              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import ast
import json
import shlex
import sqlite3
import subprocess
import tempfile
import shutil
import re
import time
import textwrap
import importlib.util
from datetime import datetime
from typing import Optional
import threading
import signal
import select
import urllib.request
import urllib.error

# ══════════════════════════════════════════════════════════════
# READLINE SETUP — arrow keys, history, cursor movement
# ══════════════════════════════════════════════════════════════
try:
    import readline as _rl
    import atexit as _atexit

    _HIST_FILE = os.path.expanduser("~/.hackers_ai_history")
    _rl.set_history_length(1000)
    try:
        _rl.read_history_file(_HIST_FILE)
    except FileNotFoundError:
        pass
    _atexit.register(_rl.write_history_file, _HIST_FILE)

    # Use emacs editing mode — this gives arrow-key history/movement for free
    # without double-binding that corrupts the terminal on rapid key presses.
    _rl.parse_and_bind("set editing-mode emacs")
    _rl.parse_and_bind("tab: complete")
    # Only add bindings not already covered by emacs mode
    _rl.parse_and_bind(r'"\e[1;5C": forward-word')     # Ctrl+→
    _rl.parse_and_bind(r'"\e[1;5D": backward-word')    # Ctrl+←
    _READLINE_OK = True
except ImportError:
    _READLINE_OK = False

# ══════════════════════════════════════════════════════════════
# TELEGRAM NOTIFIER
# ══════════════════════════════════════════════════════════════

TELEGRAM_CONFIG_PATH = os.path.expanduser("~/.hackers_ai_telegram.json")

def _tg_config_load() -> dict:
    if not os.path.exists(TELEGRAM_CONFIG_PATH):
        return {}
    try:
        with open(TELEGRAM_CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _tg_config_save(data: dict):
    with open(TELEGRAM_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TELEGRAM_CONFIG_PATH, 0o600)

class TelegramBot:
    """
    Two-way Telegram bridge — runs a long-poll loop in a background thread.
    Only the configured user_id can send commands. All others are silently ignored.

    Flow:
      User sends message → bot receives → passes to CLI.process() or _handle_slash()
      → captures stdout → sends result back to Telegram

    Commands via Telegram:
      Any text           → passed to the AI agent (same as typing in terminal)
      /slash commands    → handled by CLI._handle_slash()
      /tg_stop           → stop the bot remotely
    """

    BASE = "https://api.telegram.org/bot{token}/{method}"
    MAX_MSG = 4000   # Telegram message char limit (4096 minus buffer)

    def __init__(self):
        cfg              = _tg_config_load()
        self.token       = cfg.get("token", "")
        self.user_id     = str(cfg.get("user_id", ""))
        self.enabled     = cfg.get("enabled", False) and bool(self.token) and bool(self.user_id)
        self._offset     = 0
        self._thread: Optional[threading.Thread] = None
        self._stop_evt   = threading.Event()
        self._cli        = None   # set by CLI after init
        self._busy       = threading.Lock()   # prevent parallel command execution
        # pending plan confirmations: callback_id → threading.Event + result
        self._pending_confirms: dict = {}   # {callback_id: {"event": Event, "result": bool}}

    def _reload(self):
        cfg          = _tg_config_load()
        self.token   = cfg.get("token", "")
        self.user_id = str(cfg.get("user_id", ""))
        self.enabled = cfg.get("enabled", False) and bool(self.token) and bool(self.user_id)

    # ── HTTP helpers ───────────────────────────────────────────
    def _api(self, method: str, payload: dict = None, timeout: int = 35) -> dict:
        url  = self.BASE.format(token=self.token, method=method)
        data = json.dumps(payload or {}).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    def send(self, text: str, parse_mode: str = "HTML") -> tuple:
        """Send a message to the configured user. Returns (ok, err)."""
        if not self.token or not self.user_id:
            return False, "Not configured"
        # Strip ANSI colour codes before sending to Telegram
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        # Split into chunks if over limit
        chunks = [clean[i:i+self.MAX_MSG] for i in range(0, max(len(clean),1), self.MAX_MSG)]
        for chunk in chunks:
            body = self._api("sendMessage", {
                "chat_id":    self.user_id,
                "text":       chunk,
                "parse_mode": parse_mode,
            })
            if not body.get("ok"):
                # Retry as plain text if HTML parse failed
                body = self._api("sendMessage", {
                    "chat_id": self.user_id,
                    "text":    chunk,
                })
            if not body.get("ok"):
                return False, body.get("description", "send failed")
        return True, ""

    def send_typing(self):
        self._api("sendChatAction", {"chat_id": self.user_id, "action": "typing"})

    def send_with_keyboard(self, text: str, callback_id: str) -> int:
        """Send a message with ✅ Y / ❌ N inline buttons. Returns message_id."""
        # Strip ANSI and hard-truncate to stay within Telegram's 4096-char limit
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        if len(clean) > 3800:
            clean = clean[:3800] + "\n...[truncated]"
        markup = {
            "inline_keyboard": [[
                {"text": "✅  Yes, execute",  "callback_data": f"{callback_id}:yes"},
                {"text": "❌  No, cancel",    "callback_data": f"{callback_id}:no"},
            ]]
        }
        # Try HTML first, fall back to plain text on parse error
        body = self._api("sendMessage", {
            "chat_id":      self.user_id,
            "text":         clean,
            "parse_mode":   "HTML",
            "reply_markup": markup,
        })
        if not body.get("ok"):
            plain = re.sub(r"<[^>]+>", "", clean)
            body = self._api("sendMessage", {
                "chat_id":      self.user_id,
                "text":         plain,
                "reply_markup": markup,
            })
        return body.get("result", {}).get("message_id", 0)

    def _answer_callback(self, callback_query_id: str):
        """Acknowledge a button tap so Telegram removes the loading spinner."""
        self._api("answerCallbackQuery", {"callback_query_id": callback_query_id})

    def _edit_reply_markup(self, message_id: int, new_text: str):
        """Replace the inline keyboard with a plain confirmation line."""
        self._api("editMessageText", {
            "chat_id":    self.user_id,
            "message_id": message_id,
            "text":       new_text,
            "parse_mode": "HTML",
        })

    # ── Configuration ──────────────────────────────────────────
    def configure(self, token: str, user_id: str) -> tuple:
        data = {"token": token, "user_id": user_id, "enabled": True}
        _tg_config_save(data)
        self._reload()
        ok, err = self.send(
            "✅ <b>Hackers AI connected!</b>\n"
            "You can now send commands here and I will execute them on your machine.\n"
            "Type anything to get started. Use /status to check the agent."
        )
        if not ok:
            data["enabled"] = False
            _tg_config_save(data)
            self._reload()
        return ok, err

    def disable(self):
        cfg = _tg_config_load()
        cfg["enabled"] = False
        _tg_config_save(cfg)
        self.stop()
        self._reload()

    def enable(self):
        cfg = _tg_config_load()
        cfg["enabled"] = True
        _tg_config_save(cfg)
        self._reload()

    @property
    def status_str(self) -> str:
        if not self.token:
            return "not configured"
        running = self._thread and self._thread.is_alive()
        state   = "running" if running else ("enabled" if self.enabled else "disabled")
        return f"{state}  user_id={self.user_id or '?'}"

    # ── Long-poll loop ─────────────────────────────────────────
    def start(self, cli):
        """Start the background polling thread. cli = CLI instance."""
        if self._thread and self._thread.is_alive():
            return
        self._cli      = cli
        self._stop_evt.clear()
        self._thread   = threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll")
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _poll_loop(self):
        """Long-poll getUpdates → dispatch messages and callback_query button taps."""
        while not self._stop_evt.is_set():
            try:
                body = self._api("getUpdates", {
                    "offset":          self._offset,
                    "timeout":         25,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=30)

                if not body.get("ok"):
                    time.sleep(3)
                    continue

                for update in body.get("result", []):
                    self._offset = update["update_id"] + 1

                    # ── Inline keyboard button tap ─────────────
                    cq = update.get("callback_query")
                    if cq:
                        cq_id      = cq.get("id", "")
                        cq_from    = str(cq.get("from", {}).get("id", ""))
                        cq_data    = cq.get("data", "")
                        self._answer_callback(cq_id)  # dismiss spinner
                        if cq_from != self.user_id:
                            continue
                        # data format: "<callback_id>:yes" or "<callback_id>:no"
                        if ":" in cq_data:
                            cb_key, choice = cq_data.rsplit(":", 1)
                            if cb_key in self._pending_confirms:
                                entry = self._pending_confirms[cb_key]
                                entry["result"] = (choice == "yes")
                                entry["event"].set()
                        continue

                    # ── Regular text message ───────────────────
                    msg = update.get("message", {})
                    if not msg:
                        continue
                    from_id = str(msg.get("from", {}).get("id", ""))
                    text    = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    if from_id != self.user_id:
                        self._api("sendMessage", {
                            "chat_id": from_id,
                            "text":    "⛔ Unauthorised.",
                        })
                        continue
                    threading.Thread(
                        target=self._dispatch,
                        args=(text,),
                        daemon=True
                    ).start()

            except Exception:
                time.sleep(3)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI colour codes so Telegram renders cleanly."""
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    # Lines that are pure UI chrome — suppress entirely in Telegram output
    _TG_SUPPRESS_PATTERNS = (
        "╔══ EXECUTION PLAN", "╠══ STEPS", "╚══",
        "╭─ Hackers AI", "╰─", "╭─ Suggested next", "╭─",
        "═══════", "──────",
        "[→] Summarizing", "[→] Planning", "[→] Resolving",
        "[✓] Context resolved", "Auto-confirming",
        "Telegram] Waiting", "[Telegram] YES", "[Telegram] NO",
        "└─", "┌─",   # command box borders (contain exit:N and $ cmd lines)
    )

    def _flush_to_tg(self, buf: list, force: bool = False) -> list:
        """
        Accumulate output lines and send to Telegram.
        Suppresses pure UI-chrome lines (box-drawing headers/footers).
        Flushes at natural output boundaries or when forced.
        Returns the remaining un-sent lines.
        """
        if not buf:
            return buf

        # Filter out pure chrome lines before joining
        clean_lines = []
        for raw in buf:
            stripped = self._strip_ansi(raw).strip()
            if not stripped:
                continue
            if any(stripped.startswith(p) or p in stripped
                   for p in self._TG_SUPPRESS_PATTERNS):
                continue
            clean_lines.append(stripped)

        joined = "\n".join(clean_lines).strip()

        # Decide whether to flush now
        FLUSH_TRIGGERS = (
            "▶ Step", "[MCP]",
            "Task complete", "Goodbye",
            "Planning", "Analysing", "Summarizing",
            "Context resolved", "Context from history",
            "Need more info", "Planner failed",
            "[CodeGen]", "[Installer]", "[Phase",
        )
        should_flush = (
            force
            or len(buf) >= 5
            or any(t in joined for t in FLUSH_TRIGGERS)
        )
        if not should_flush:
            return list(buf)   # return a COPY — not the same list reference

        if joined:
            if len(joined) > 3800:
                joined = joined[:3800] + "\n...[truncated]"
            self.send(f"<pre>{joined}</pre>")
        return []  # always return a new empty list, never the original buf

    def _dispatch(self, text: str):
        """
        Handle one incoming Telegram message end-to-end:
          • Sets cli._tg_mode = True  →  all input() prompts auto-confirm
          • Tees stdout to Telegram in real-time chunks
          • Resets cli._tg_mode = False when done
        """
        if not self._cli:
            return

        if not self._busy.acquire(blocking=False):
            self.send("⏳ Busy with another command — please wait.")
            return

        try:
            self.send_typing()
            tl = text.strip().lower()

            # ── Built-in bot commands (no pipeline needed) ─────
            if tl in ("/tg_stop", "/tg stop"):
                self.send("🛑 Stopping Telegram bridge. Bye!")
                self.stop()
                return

            if tl == "/status":
                p   = self._cli.profile
                mcp = self._cli._mcp_client.name if self._cli._mcp_client else "none"
                self.send(
                    "<b>🤖 Hackers AI Status</b>\n"
                    f"Host   : {p.get('hostname','')}  IP: {p.get('ip','')}\n"
                    f"User   : {p.get('whoami','')}  Root: {p.get('root','')}\n"
                    f"CWD    : {self._cli.cwd}\n"
                    f"Target : {self._cli.sticky_target or 'none'}\n"
                    f"MCP    : {mcp}\n"
                    f"Model  : {self._cli.model}\n"
                    f"DryRun : {self._cli.dry_run}"
                )
                return

            if tl == "/help":
                cmds = "\n".join(
                    f"{k:<20} {v}" for k, v in self._cli.SLASH_COMMANDS.items()
                )
                self.send(
                    "<b>Commands:</b>\n<pre>"
                    + cmds
                    + "\n\n/status          — agent status"
                    + "\n/tg_stop         — stop this bot</pre>"
                )
                return

            if tl in ("/exit", "/quit"):
                self.send("❌ /exit is disabled via Telegram.")
                return

            # ── Activate no-prompt mode on CLI + engine ────────
            self._cli._tg_mode = True

            # ── Live-streaming stdout tee ──────────────────────
            buf      = []
            buf_lock = threading.Lock()
            orig     = sys.stdout
            bot      = self   # closure ref

            class _TeeStream:
                def write(self_, s: str):
                    orig.write(s)
                    orig.flush()
                    with buf_lock:
                        buf.append(s)
                        remaining = bot._flush_to_tg(buf, force=False)
                        buf.clear()
                        buf.extend(remaining)
                def flush(self_):
                    orig.flush()
                def isatty(self_):
                    return False
                def force_flush(self_):
                    """Drain the tee buffer immediately — call before blocking waits."""
                    with buf_lock:
                        bot._flush_to_tg(buf, force=True)
                        buf.clear()

            tee_stream = _TeeStream()
            sys.stdout  = tee_stream
            # Expose force_flush on the bot so _confirm_plan can drain before blocking
            bot._force_flush = tee_stream.force_flush
            err_str    = None
            try:
                self._cli._inject_profile_context()
                if text.startswith("/"):
                    slug = text.strip().split()[0].lower()
                    self._cli._handle_slash(text)
                else:
                    self._cli.process(text)
            except Exception as e:
                err_str = str(e)
                orig.write(f"\n[TG ERROR] {e}\n")
            finally:
                sys.stdout          = orig
                self._cli._tg_mode  = False
                self._force_flush   = None   # detach tee reference

            # Flush anything still in the buffer
            with buf_lock:
                self._flush_to_tg(buf, force=True)
                buf.clear()

            if err_str:
                self.send(f"⚠️ <b>Error:</b> <pre>{err_str[:500]}</pre>")

        finally:
            self._busy.release()

    # ── Notification helpers (kept for backward compat) ────────
    def notify_task_done(self, task: str, summary: str, elapsed: float = 0):
        if not self.enabled:
            return
        elapsed_str = f"{elapsed:.0f}s" if elapsed else ""
        msg = (
            f"<b>✅ Task Done</b>\n"
            + (f"⏱ {elapsed_str}\n" if elapsed_str else "")
            + f"<b>Task:</b> {task[:150]}\n"
            f"<b>Result:</b>\n<pre>{summary[:600]}</pre>"
        )
        self.send(msg)

    def notify_step_error(self, step: str, error: str):
        if not self.enabled:
            return
        self.send(
            f"<b>⚠️ Step Failed</b>\n"
            f"<b>Step:</b> {step[:120]}\n"
            f"<b>Error:</b> <pre>{error[:300]}</pre>"
        )


# ══════════════════════════════════════════════════════════════
# DEEPSEEK PROXY CLIENT
# ══════════════════════════════════════════════════════════════

PROXY_BASE_URL = os.environ.get("HACKERS_AI_PROXY", "http://localhost:8765")
PROXY_MODEL    = "deepseek-chat"

class FreeLLM:
    def __init__(self, model: str = PROXY_MODEL):
        self.model = model

    def ask(self, prompt: str, system: str = "") -> str:
        messages = [{"role": "user", "content": prompt}]
        payload  = {
            "model":      self.model,
            "max_tokens": 4096,
            "messages":   messages,
        }
        if system:
            payload["system"] = system

        data    = json.dumps(payload).encode("utf-8")
        url     = f"{PROXY_BASE_URL}/v1/messages"
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         "local-proxy-key",
            "anthropic-version": "2023-06-01",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            for block in body.get("content", []):
                if block.get("type") == "text":
                    text = block["text"]
                    text = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', text)
                    return text
            return ""
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"[FreeLLM] Cannot reach proxy at {PROXY_BASE_URL}. "
                f"Is server.py running?  ({e})"
            )
        except Exception as e:
            raise RuntimeError(f"[FreeLLM] Request failed: {e}")

# ══════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIG
# ══════════════════════════════════════════════════════════════

DB_PATH       = os.path.expanduser("~/.hackers_ai.db")
MCP_CONFIG_PATH = os.path.expanduser("~/.hackers_ai_mcp.json")
MAX_HISTORY   = 10
MAX_RETRIES   = 3
DEFAULT_MODEL = PROXY_MODEL
VERSION       = "7.3.0"

LOCAL_SCOPE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"
    r"0\.0\.0\.0|::1)$",
    re.IGNORECASE
)

BANNER = r"""
  ██╗  ██╗ █████╗  ██████╗██╗  ██╗███████╗██████╗ ███████╗     █████╗ ██╗
  ██║  ██║██╔══██╗██╔════╝██║ ██╔╝██╔════╝██╔══██╗██╔════╝    ██╔══██╗██║
  ███████║███████║██║     █████╔╝ █████╗  ██████╔╝███████╗    ███████║██║
  ██╔══██║██╔══██║██║     ██╔═██╗ ██╔══╝  ██╔══██╗╚════██║    ██╔══██║██║
  ██║  ██║██║  ██║╚██████╗██║  ██╗███████╗██║  ██║███████║    ██║  ██║██║
  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝    ╚═╝  ╚═╝╚═╝
         Advanced Linux Agent · General Purpose + Authorized Pentesting
"""

COLORS = {
    "reset":   "\033[0m",  "bold":    "\033[1m",   "dim":     "\033[2m",
    "red":     "\033[91m", "green":   "\033[92m",  "yellow":  "\033[93m",
    "blue":    "\033[94m", "magenta": "\033[95m",  "cyan":    "\033[96m",
    "white":   "\033[97m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"

# ══════════════════════════════════════════════════════════════
# SECTION 1.5 — MCP CONFIG (Claude Desktop Style)
# ══════════════════════════════════════════════════════════════

MCP_CONFIG_TEMPLATE = """{
  "mcpServers": {
    "example-server": {
      "command": "/usr/bin/python3",
      "args": [
        "/path/to/mcp_server.py"
      ],
      "env": {
        "SOME_VAR": "value"
      }
    }
  }
}
"""

def _mcp_config_load() -> dict:
    """Load ~/.hackers_ai_mcp.json, return empty structure if missing/invalid."""
    if not os.path.exists(MCP_CONFIG_PATH):
        return {"mcpServers": {}}
    try:
        with open(MCP_CONFIG_PATH, "r") as f:
            data = json.load(f)
        if not isinstance(data.get("mcpServers"), dict):
            data["mcpServers"] = {}
        return data
    except Exception:
        return {"mcpServers": {}}

def _mcp_config_save(data: dict):
    """Save config to disk."""
    with open(MCP_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)

def _mcp_config_ensure():
    """Create config file with template if it doesn't exist."""
    if not os.path.exists(MCP_CONFIG_PATH):
        with open(MCP_CONFIG_PATH, "w") as f:
            f.write(MCP_CONFIG_TEMPLATE)

def _mcp_config_open_editor():
    """Open MCP config in the user's preferred editor."""
    _mcp_config_ensure()
    # Pick editor: $VISUAL > $EDITOR > nano > vi
    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or shutil.which("nano")
        or shutil.which("vi")
        or "nano"
    )
    # If running as root via sudo, open as the real user so the GUI editor works
    sudo_user = os.environ.get("SUDO_USER")
    try:
        if sudo_user and os.geteuid() == 0:
            env = os.environ.copy()
            env["HOME"] = os.path.expanduser(f"~{sudo_user}")
            subprocess.run(
                ["su", "-c", f"{shlex.quote(editor)} {shlex.quote(MCP_CONFIG_PATH)}", sudo_user],
                env=env
            )
        else:
            subprocess.run([editor, MCP_CONFIG_PATH])
    except Exception as e:
        print(c("red", f"  Could not open editor: {e}"))
        print(c("dim",  f"  Edit manually: {MCP_CONFIG_PATH}"))

# ══════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE  (MCP tables simplified — no URL storage)
# ══════════════════════════════════════════════════════════════

class MemoryDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    model TEXT, timestamp TEXT DEFAULT (datetime('now'))
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL, step INTEGER NOT NULL,
                    tool TEXT, command TEXT, output TEXT, status TEXT,
                    timestamp TEXT DEFAULT (datetime('now'))
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS target_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL, note TEXT NOT NULL,
                    timestamp TEXT DEFAULT (datetime('now'))
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS authorized_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT UNIQUE NOT NULL,
                    added TEXT DEFAULT (datetime('now'))
                )""")
            # MCP: only track which server name is "active" — config is in JSON file
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_active (
                    id      INTEGER PRIMARY KEY CHECK (id = 1),
                    name    TEXT NOT NULL
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_tool_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    server    TEXT,
                    tool      TEXT,
                    args      TEXT,
                    result    TEXT,
                    status    TEXT,
                    timestamp TEXT DEFAULT (datetime('now'))
                )""")
            conn.commit()

    # ── Conversation ───────────────────────────────────────
    def add_message(self, role: str, content: str, model: str = DEFAULT_MODEL):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, model) VALUES (?,?,?)",
                (role, content, model))
            conn.commit()

    def get_history(self, limit: int = MAX_HISTORY) -> list:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def clear_history(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM conversations")
            conn.commit()

    def get_last_session_summary(self) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content, timestamp FROM conversations WHERE role='user' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        return f"{row[0][:80]}  [{row[1][:16]}]" if row else None

    # ── Notes ──────────────────────────────────────────────
    def add_note(self, target: str, note: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO target_notes (target,note) VALUES (?,?)", (target, note))
            conn.commit()

    def get_notes(self, target: str = None) -> list:
        with sqlite3.connect(self.db_path) as conn:
            if target:
                rows = conn.execute(
                    "SELECT target,note,timestamp FROM target_notes WHERE target LIKE ? ORDER BY id DESC",
                    (f"%{target}%",)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT target,note,timestamp FROM target_notes ORDER BY id DESC LIMIT 50"
                ).fetchall()
        return [{"target": r[0], "note": r[1], "timestamp": r[2]} for r in rows]

    def delete_notes(self, target: str = None):
        with sqlite3.connect(self.db_path) as conn:
            if target:
                conn.execute("DELETE FROM target_notes WHERE target LIKE ?", (f"%{target}%",))
            else:
                conn.execute("DELETE FROM target_notes")
            conn.commit()

    def log_task_step(self, task_id, step, tool, command, output, status):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO task_memory (task_id,step,tool,command,output,status) VALUES (?,?,?,?,?,?)",
                (task_id, step, tool, command, output[:2000], status))
            conn.commit()

    # ── Authorized targets ─────────────────────────────────
    def add_authorized_target(self, target: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO authorized_targets (target) VALUES (?)", (target,))
            conn.commit()

    def is_authorized(self, target: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM authorized_targets WHERE ? LIKE '%'||target||'%' OR target LIKE '%'||?||'%'",
                (target, target)).fetchone()
        return row is not None

    def get_authorized_targets(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT target, added FROM authorized_targets ORDER BY id DESC").fetchall()
        return [{"target": r[0], "added": r[1]} for r in rows]

    def remove_authorized_target(self, target: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM authorized_targets WHERE target=?", (target,))
            conn.commit()

    # ── MCP active server (name only) ──────────────────────
    def set_mcp_active(self, name: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mcp_active (id, name) VALUES (1, ?)", (name,))
            conn.commit()

    def get_mcp_active_name(self) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT name FROM mcp_active WHERE id=1").fetchone()
        return row[0] if row else None

    def clear_mcp_active(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mcp_active WHERE id=1")
            conn.commit()

    # ── MCP tool log ───────────────────────────────────────
    def log_mcp_call(self, server: str, tool: str, args: str, result: str, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO mcp_tool_log (server,tool,args,result,status) VALUES (?,?,?,?,?)",
                (server, tool, args[:500], result[:2000], status))
            conn.commit()

    # ── Session export ─────────────────────────────────────
    def export_session(self, name: str = None) -> str:
        history = self.get_history(200)
        with sqlite3.connect(self.db_path) as conn:
            tasks = conn.execute(
                "SELECT task_id,step,tool,command,output,status,timestamp FROM task_memory ORDER BY id"
            ).fetchall()
            notes = conn.execute(
                "SELECT target,note,timestamp FROM target_notes ORDER BY id").fetchall()
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = name or f"Hackers AI Session — {ts}"
        lines = [f"# {title}", f"*Exported: {ts}*", ""]
        if notes:
            lines += ["## Target Notes", ""]
            for n in notes:
                lines.append(f"- **{n[0]}** ({n[2][:16]}): {n[1]}")
            lines.append("")
        if tasks:
            lines += ["## Command Log", ""]
            cur_task = None
            for t in tasks:
                if t[0] != cur_task:
                    cur_task = t[0]
                    lines.append(f"### Task {cur_task}")
                icon = "✓" if t[5] == "success" else "✗"
                lines.append(f"**Step {t[1]}** `{t[3]}` — {icon} {t[5]}")
                if t[4] and t[4].strip():
                    lines.append("```\n" + t[4][:500] + "\n```")
            lines.append("")
        if history:
            lines += ["## Conversation", ""]
            for h in history:
                role = "**You**" if h["role"] == "user" else "**AI**"
                lines.append(f"{role}: {h['content'][:300]}")
        return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# SECTION 3 — SCOPE GUARD
# ══════════════════════════════════════════════════════════════

class ScopeGuard:
    PENTEST_VERBS = re.compile(
        r"\b(scan|exploit|fuzz|brute|inject|sqli|xss|nikto|nmap|nuclei|"
        r"gobuster|dalfox|sqlmap|hydra|ffuf|wfuzz|dirb|dirsearch|"
        r"enumerate|recon|attack|crack|payload|bypass)\b",
        re.IGNORECASE
    )
    TARGET_RE = re.compile(
        r"(https?://[^\s]+|"
        r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b|"
        r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)",
        re.IGNORECASE
    )

    def __init__(self, memory: MemoryDB):
        self.memory = memory

    def _extract_host(self, target: str) -> str:
        target = re.sub(r"^https?://", "", target).split("/")[0].split(":")[0]
        return target.strip().lower()

    def _is_local(self, host: str) -> bool:
        return bool(LOCAL_SCOPE.match(host))

    def check(self, user_input: str, sticky_target: str = "") -> tuple:
        if not self.PENTEST_VERBS.search(user_input):
            return True, "general task", ""
        targets = self.TARGET_RE.findall(user_input)
        if not targets and sticky_target:
            targets = self.TARGET_RE.findall(sticky_target)
        if not targets:
            return True, "no external target", ""
        host = self._extract_host(targets[0])
        if self._is_local(host):
            return True, "local target", host
        if self.memory.is_authorized(host):
            return True, "authorized target", host
        return None, host, host

# ══════════════════════════════════════════════════════════════
# SECTION 4 — SYSTEM PROFILER
# ══════════════════════════════════════════════════════════════

def _quick_cmd(cmd: str, timeout: int = 5) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception:
        return ""

class ToolInspector:
    PREFER_SHORT_HELP = {"nmap", "sqlmap", "hydra", "medusa", "dalfox", "xsstrike"}
    SPECIAL_HELP = {
        "msfconsole": "msfconsole --help 2>&1 | head -30",
        "metasploit-framework": "msfconsole --help 2>&1 | head -30",
    }

    def __init__(self):
        self._cache: dict = {}

    def get_help(self, tool: str, max_chars: int = 2000) -> str:
        if tool in self._cache:
            return self._cache[tool]
        if not shutil.which(tool):
            return ""
        if tool in self.SPECIAL_HELP:
            out = _quick_cmd(self.SPECIAL_HELP[tool], timeout=10)
        else:
            flags = ["-h", "--help"] if tool in self.PREFER_SHORT_HELP else ["--help", "-h"]
            out = ""
            for flag in flags:
                try:
                    r = subprocess.run([tool, flag], capture_output=True, text=True, timeout=6)
                    out = (r.stdout + r.stderr).strip()
                    if out and len(out) > 50:
                        break
                except Exception:
                    continue
        result = out[:max_chars] if out else f"(help unavailable for {tool})"
        self._cache[tool] = result
        return result

    def get_version(self, tool: str) -> str:
        if not shutil.which(tool):
            return ""
        for flag in ["--version", "-version", "-V", "version"]:
            try:
                r = subprocess.run([tool, flag], capture_output=True, text=True, timeout=4)
                out = (r.stdout + r.stderr).strip()
                if out:
                    return out.splitlines()[0][:120]
            except Exception:
                continue
        return ""

    def identify_httpx(self) -> str:
        if shutil.which("httpx-toolkit"):
            return "scanner"
        if not shutil.which("httpx"):
            return "none"
        try:
            r = subprocess.run(["httpx", "--version"], capture_output=True, text=True, timeout=4)
            out = (r.stdout + r.stderr).lower()
            if "projectdiscovery" in out or "httpx" in out and "next generation" not in out:
                r2 = subprocess.run(["httpx", "-h"], capture_output=True, text=True, timeout=4)
                h = (r2.stdout + r2.stderr).lower()
                if "-title" in h or "-tech-detect" in h or "-status-code" in h:
                    return "scanner"
            return "client"
        except Exception:
            return "none"

class WordlistFinder:
    CANDIDATES = {
        "xss": [
            "/usr/share/seclists/Fuzzing/XSS/XSS-Reflected.txt",
            "/usr/share/seclists/Fuzzing/XSS/XSS-Stored.txt",
            "/usr/share/seclists/Fuzzing/XSS/XSS-Bypass-Strings.txt",
            "/usr/share/seclists/Fuzzing/XSS/XSS-Jhaddix.txt",
            "/usr/share/wordlists/wfuzz/Injections/XSS.txt",
            "/usr/share/wfuzz/wordlist/Injections/XSS.txt",
        ],
        "sqli": [
            "/usr/share/seclists/Fuzzing/SQLi/Generic-SQLi.txt",
            "/usr/share/seclists/Fuzzing/SQLi/quick-SQLi.txt",
            "/usr/share/seclists/Fuzzing/SQLi/MySQL-SQLi-Login-Bypass.txt",
            "/usr/share/wordlists/wfuzz/Injections/SQL.txt",
            "/usr/share/wfuzz/wordlist/Injections/SQL.txt",
        ],
        "dirs": [
            "/usr/share/seclists/Discovery/Web-Content/common.txt",
            "/usr/share/seclists/Discovery/Web-Content/raft-medium-directories.txt",
            "/usr/share/wordlists/dirb/common.txt",
            "/usr/share/wordlists/dirbuster/directory-list-2.3-small.txt",
        ],
        "dirs_big": [
            "/usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt",
            "/usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt",
        ],
        "passwords": [
            "/usr/share/wordlists/rockyou.txt",
            "/usr/share/seclists/Passwords/Common-Credentials/10k-most-common.txt",
            "/usr/share/seclists/Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt",
        ],
        "subdomains": [
            "/usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt",
            "/usr/share/seclists/Discovery/DNS/bitquark-subdomains-top100000.txt",
            "/usr/share/seclists/Discovery/DNS/namelist.txt",
        ],
        "usernames": [
            "/usr/share/seclists/Usernames/top-usernames-shortlist.txt",
            "/usr/share/seclists/Usernames/Names/names.txt",
        ],
        "lfi": [
            "/usr/share/seclists/Fuzzing/LFI/LFI-Jhaddix.txt",
            "/usr/share/seclists/Fuzzing/LFI/LFI-gracefulsecurity-linux.txt",
        ],
        "params": [
            "/usr/share/seclists/Discovery/Web-Content/burp-parameter-names.txt",
            "/usr/share/seclists/Discovery/Web-Content/api/objects.txt",
        ],
    }

    def scan(self) -> dict:
        found = {}
        for cat, paths in self.CANDIDATES.items():
            for p in paths:
                if os.path.exists(p):
                    found[cat] = p
                    break
        return found

    def format_for_prompt(self, found: dict) -> str:
        if not found:
            return "WORDLISTS: None found. Do not use -w flag without confirming path exists."
        lines = [f"  {cat:<12}: {path}" for cat, path in found.items()]
        return "AVAILABLE WORDLISTS (use ONLY these exact verified paths):\n" + "\n".join(lines)

class SystemProfiler:
    def __init__(self):
        self.inspector = ToolInspector()

    @staticmethod
    def is_root() -> bool:
        return os.geteuid() == 0

    @staticmethod
    def get_available_tools() -> list:
        pentest_tools = [
            "nmap", "masscan", "rustscan", "naabu", "netdiscover",
            "subfinder", "amass", "assetfinder", "findomain", "sublist3r",
            "dnsenum", "dnsrecon", "fierce", "dnsx", "theharvester",
            "recon-ng", "shodan", "whois", "dig", "host", "nslookup",
            "nikto", "nuclei", "wpscan", "joomscan", "whatweb",
            "wafw00f", "sslscan", "sslyze", "testssl", "searchsploit",
            "gobuster", "dirb", "dirsearch", "feroxbuster", "ffuf", "wfuzz",
            "katana", "httpx-toolkit", "arjun",
            "sqlmap", "commix", "dalfox", "xsstrike", "crlfuzz",
            "burpsuite", "zaproxy", "mitmproxy", "mitmdump",
            "hashcat", "john", "hydra", "medusa", "ncrack",
            "cewl", "crunch", "cupp", "hash-identifier", "hashid",
            "aircrack-ng", "airmon-ng", "airodump-ng", "aireplay-ng",
            "wifite", "kismet", "reaver",
            "msfconsole", "msfvenom", "pwncat", "evil-winrm",
            "crackmapexec", "netexec", "impacket-scripts",
            "rpcclient", "enum4linux", "enum4linux-ng",
            "smbclient", "smbmap", "ldapsearch",
            "wireshark", "tshark", "tcpdump", "netcat", "nc",
            "ettercap", "responder", "bettercap", "hping3",
            "fping", "arping",
            "linpeas", "winpeas", "linux-exploit-suggester", "pspy",
            "bloodhound", "neo4j",
            "binwalk", "strings", "file", "ltrace", "strace",
            "gdb", "radare2", "r2", "ghidra", "objdump", "readelf",
            "volatility3", "foremost", "exiftool", "steghide", "checksec",
            "jadx", "apktool",
            "sherlock", "holehe", "maigret", "photon",
            "trivy", "awscli", "az", "gcloud",
            "curl", "wget", "socat", "ncat",
            "ssh", "scp", "openssl", "gpg", "proxychains4", "tor",
            "python3", "python", "pip3",
            "ruby", "perl", "go", "gcc",
            "git", "docker", "tmux",
            "jq", "base64", "xxd",
        ]
        found = [t for t in pentest_tools if shutil.which(t)]
        inspector = ToolInspector()
        httpx_type = inspector.identify_httpx()
        if httpx_type == "scanner":
            binary = "httpx-toolkit" if shutil.which("httpx-toolkit") else "httpx"
            if binary not in found:
                found.append(binary)
        elif httpx_type == "client":
            if "httpx" not in found:
                found.append("httpx-encode-client")
        return list(set(found))

    def profile(self) -> dict:
        return {
            "uname":    _quick_cmd("uname -a"),
            "hostname": _quick_cmd("hostname"),
            "whoami":   _quick_cmd("whoami"),
            "root":     self.is_root(),
            "distro":   _quick_cmd(
                "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
            ),
            "cpu":      _quick_cmd(
                "lscpu 2>/dev/null | grep 'Model name' | cut -d: -f2 | xargs"
            ),
            "ram":      _quick_cmd(
                "free -h 2>/dev/null | awk '/^Mem:/{print $2\" total, \"$3\" used, \"$4\" free\"}'"
            ),
            "disk":     _quick_cmd(
                "df -h / 2>/dev/null | awk 'NR==2{print $2\" total, \"$3\" used, \"$4\" free\"}'"
            ),
            "ip":       _quick_cmd("hostname -I 2>/dev/null | awk '{print $1}'"),
            "kernel":   _quick_cmd("uname -r"),
            "arch":     _quick_cmd("uname -m"),
            "shell":    os.environ.get("SHELL", "bash"),
            "available_tools": self.get_available_tools(),
        }

# ══════════════════════════════════════════════════════════════
# SECTION 5 — SPINNER
# ══════════════════════════════════════════════════════════════

class Spinner:
    FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def __init__(self, label: str = "Working"):
        self.label   = label
        self._stop   = threading.Event()
        self._thread = None

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(
                "\r  " + COLORS['cyan'] + frame + COLORS['reset'] + "  " + self.label + "...  "
            )
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
        sys.stdout.write("\r" + " " * (len(self.label) + 12) + "\r")
        sys.stdout.flush()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)

    def __enter__(self):
        self.start(); return self

    def __exit__(self, *_):
        self.stop()

# ══════════════════════════════════════════════════════════════
# SECTION 6 — COMMAND EXECUTOR
# ══════════════════════════════════════════════════════════════

class CommandExecutor:
    @staticmethod
    def as_user(command: str) -> str:
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user or os.geteuid() != 0:
            return command
        env_prefix = (
            f"DISPLAY={os.environ.get('DISPLAY',':0')} "
            f"XAUTHORITY=/home/{sudo_user}/.Xauthority "
        )
        escaped = command.replace("'", "'\\''")
        return f"su -c '{env_prefix}{escaped}' {sudo_user}"

    def run(self, command: str, timeout: int = 180,
            label: str = "", lock: threading.Lock = None,
            cwd: str = None) -> dict:
        tag    = f"[{label}] " if label else ""
        _print = (lambda msg: _locked_print(lock, msg)) if lock else print
        effective_cwd = cwd if (cwd and os.path.isdir(cwd)) else None

        stdout_lines = []
        start        = time.time()
        process      = None

        # In Telegram tee mode stdout.isatty() returns False — collect all output
        # silently and emit the whole block as one print() → one Telegram message.
        _tg_mode = not getattr(sys.stdout, 'isatty', lambda: True)()

        if not _tg_mode:
            _print(c("dim", f"\n  ┌─ {tag}$ {command}"))

        def _kill():
            if process and process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    time.sleep(3)
                    if process.poll() is None:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass

        try:
            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                cwd=effective_cwd,
                preexec_fn=os.setsid
            )
            try:
                for line in iter(process.stdout.readline, ''):
                    line = line.rstrip()
                    if line:
                        if not _tg_mode:
                            _print(c("dim", f"  │ {tag}") + line)
                        stdout_lines.append(line)
            except KeyboardInterrupt:
                if not _tg_mode:
                    _print(c("yellow", f"\n  ├─ {tag}⚡ Ctrl+C — cancelling..."))
                _kill()
                elapsed = round(time.time() - start, 2)
                if _tg_mode:
                    block = (
                        f"$ {tag}{command}\n"
                        + ("\n".join(stdout_lines) + "\n" if stdout_lines else "")
                        + f"✗ Cancelled ({elapsed}s)"
                    )
                    _print(block)
                else:
                    _print(c("yellow", f"  └─ {tag}✗ Cancelled ({elapsed}s)"))
                return {
                    "command": command, "stdout": "\n".join(stdout_lines),
                    "stderr": "Cancelled by user", "returncode": -2,
                    "success": False, "elapsed": elapsed, "cancelled": True,
                }

            process.stdout.close()
            process.wait(timeout=timeout)
            stderr_data = process.stderr.read()
            stderr_lines = stderr_data.splitlines() if stderr_data else []

            elapsed = round(time.time() - start, 2)
            success = process.returncode == 0

            if _tg_mode:
                # Emit the entire command block as ONE print — one Telegram message
                # Use plain markers (no box-drawing │) so the tee suppress filter
                # does not accidentally match and swallow the block.
                icon_str = "✓" if success else "✗"
                all_lines = stdout_lines[:]
                if not success and stderr_lines:
                    all_lines += [f"[stderr] {l}" for l in stderr_lines if l]
                block = (
                    f"$ {tag}{command}\n"
                    + ("\n".join(all_lines) + "\n" if all_lines else "")
                    + f"{icon_str} exit:{process.returncode} ({elapsed}s)"
                )
                _print(block)
            else:
                icon = c("green", "✓") if success else c("red", "✗")
                _print(c("dim", f"  └─ {tag}{icon} exit:{process.returncode} ({elapsed}s)"))
            return {
                "command":    command,
                "stdout":     "\n".join(stdout_lines),
                "stderr":     "\n".join(stderr_lines),
                "returncode": process.returncode,
                "success":    success,
                "elapsed":    elapsed,
                "cancelled":  False,
            }
        except subprocess.TimeoutExpired:
            _kill()
            if _tg_mode:
                block = (
                    f"$ {tag}{command}\n"
                    + ("\n".join(stdout_lines) + "\n" if stdout_lines else "")
                    + f"✗ Timeout ({timeout}s)"
                )
                _print(block)
            else:
                _print(c("red", f"  └─ {tag}✗ Timeout!"))
            return {"command": command, "stdout": "\n".join(stdout_lines),
                    "stderr": "Timeout", "returncode": -1,
                    "success": False, "elapsed": timeout, "cancelled": False}
        except Exception as e:
            return {"command": command, "stdout": "", "stderr": str(e),
                    "returncode": -1, "success": False, "elapsed": 0, "cancelled": False}


def _locked_print(lock: threading.Lock, *args, **kwargs):
    with lock:
        print(*args, **kwargs)

# ══════════════════════════════════════════════════════════════
# SECTION 7 — PYTHON EXECUTOR
# ══════════════════════════════════════════════════════════════

class PythonExecutor:
    _PIP_MAP = {
        "reportlab": "reportlab", "fpdf": "fpdf2", "fpdf2": "fpdf2",
        "PIL": "Pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4",
        "sklearn": "scikit-learn", "yaml": "pyyaml", "dotenv": "python-dotenv",
        "docx": "python-docx", "pptx": "python-pptx", "openpyxl": "openpyxl",
        "requests": "requests", "pandas": "pandas", "numpy": "numpy",
        "matplotlib": "matplotlib", "flask": "flask", "fastapi": "fastapi",
        "paramiko": "paramiko", "cryptography": "cryptography",
        "pypdf": "pypdf", "pdfplumber": "pdfplumber", "qrcode": "qrcode",
        "dns": "dnspython", "scapy": "scapy", "impacket": "impacket",
        "ldap3": "ldap3", "pwnlib": "pwntools", "shodan": "shodan",
        "rich": "rich", "colorama": "colorama", "tqdm": "tqdm",
        "tabulate": "tabulate", "jinja2": "Jinja2", "aiohttp": "aiohttp",
        "httpx": "httpx", "selenium": "selenium",
    }

    def __init__(self):
        self.executor  = CommandExecutor()
        self._installed: set = set()

    def generate_and_run(self, task: str, user_input: str,
                         cwd: str = "/root", model: str = DEFAULT_MODEL) -> dict:
        print(c("cyan", f"\n  [CodeGen] ✎ Generating script: {task[:70]}"))

        prompt_base = (
            "### SYSTEM: Python3 code generator ###\n"
            "Output ONLY a ```python ... ``` fenced block. Nothing else.\n"
            "ABSOLUTE RULES:\n"
            "  1. Start your response with: ```python\n"
            "  2. End your response with: ```\n"
            "  3. NO text before or after the fence\n"
            "  4. Use ONLY spaces for indentation — NEVER tabs\n"
            "  5. Every indent level = 4 spaces\n"
            "  6. All imports at top\n"
            "  7. Script must run standalone: python3 script.py\n"
            f"  8. Save output files to: {cwd}/<filename>\n"
            "  9. Use os.makedirs(os.path.dirname(path), exist_ok=True) before writing\n"
            "  10. Final line: print success message with full output path\n"
            f"\nTASK: {task}\nUSER REQUEST: {user_input}\nWORKING DIRECTORY: {cwd}\n"
        )

        last_error = ""
        for attempt in range(1, 4):
            if attempt > 1:
                print(c("yellow", f"  [CodeGen] Retry {attempt}/3 — fixing: {last_error[:80]}"))
            full_prompt = prompt_base
            if last_error:
                full_prompt += (
                    f"\n### PREVIOUS ATTEMPT FAILED ###\n{last_error}\n"
                    "Fix ALL errors. Output only corrected Python3 code.\n"
                )
            try:
                agent = FreeLLM(model=model)
                raw   = agent.ask(full_prompt).strip()
            except Exception as e:
                last_error = str(e)
                continue
            code = self._clean_code(raw)
            if not code:
                last_error = "Empty output from LLM"
                continue
            syntax_ok, syntax_err = self._validate_syntax(code)
            if not syntax_ok:
                last_error = f"SyntaxError: {syntax_err}"
                print(c("red", f"  [CodeGen] ✗ Syntax error: {syntax_err}"))
                continue
            print(c("green", "  [CodeGen] ✓ Syntax valid — running..."))
            return self._install_and_run(code, cwd=cwd)

        return {
            "command": "codegen", "stdout": "", "elapsed": 0,
            "stderr": f"Failed after 3 attempts. Last: {last_error}",
            "returncode": -1, "success": False, "cancelled": False
        }

    @staticmethod
    def _clean_code(raw: str) -> str:
        if not raw:
            return ""
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', raw)
        raw = raw.strip()
        fence_match = re.search(r'```[^\n]*\n(.*?)(?:```|$)', raw, re.DOTALL)
        if fence_match:
            fence_line = re.search(r'```([^\n]+)\n', raw)
            if fence_line:
                tag = fence_line.group(1).strip()
                if tag and not re.match(r'^[a-zA-Z0-9]+$', tag):
                    raw = tag + "\n" + fence_match.group(1).strip()
                else:
                    raw = fence_match.group(1).strip()
            else:
                raw = fence_match.group(1).strip()
        else:
            raw = raw.strip()
        if not raw:
            return ""
        cleaned_lines = []
        for line in raw.splitlines():
            if line.strip() in ("python", "python3", "py"):
                continue
            cleaned_lines.append(line)
        raw = "\n".join(cleaned_lines)
        if not raw.strip():
            return ""
        fixed = []
        for line in raw.splitlines():
            stripped  = line.lstrip("\t")
            n_tabs    = len(line) - len(stripped)
            stripped2 = stripped.lstrip(" ")
            n_spaces  = len(stripped) - len(stripped2)
            fixed.append("    " * n_tabs + " " * n_spaces + stripped2)
        return "\n".join(fixed).rstrip("\n") + "\n"

    @staticmethod
    def _validate_syntax(code: str) -> tuple:
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"line {e.lineno}: {e.msg} — {repr(e.text)}"
        except Exception as e:
            return False, str(e)

    def _find_missing_imports(self, code: str) -> list:
        imports = re.findall(r"^(?:import|from)\s+([A-Za-z_][\w]*)", code, re.MULTILINE)
        stdlib  = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
        missing = []
        seen    = set()
        for name in imports:
            if name in seen or name in stdlib:
                continue
            seen.add(name)
            try:
                spec = importlib.util.find_spec(name)
                if spec is not None:
                    continue
            except (ModuleNotFoundError, ValueError):
                pass
            pip_pkg = self._PIP_MAP.get(name, name)
            missing.append((name, pip_pkg))
        return missing

    def _install_and_run(self, code: str, cwd: str = None) -> dict:
        missing = self._find_missing_imports(code)
        for pkg_import, pip_pkg in missing:
            if pip_pkg in self._installed:
                continue
            print(c("yellow", f"  [CodeGen] Installing dep: {pip_pkg}"))
            os.system(
                f"pip install {shlex.quote(pip_pkg)} --break-system-packages -q "
                f"--root-user-action=ignore 2>/dev/null"
            )
            self._installed.add(pip_pkg)

        tmp_path = os.path.join(
            tempfile.gettempdir(),
            f"hackers_ai_{os.getpid()}_{int(time.time() * 1000)}.py"
        )
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        try:
            result = self.executor.run(
                f"python3 {shlex.quote(tmp_path)}", timeout=120,
                cwd=cwd if (cwd and os.path.isdir(cwd)) else None
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return result

    def run(self, code: str, cwd: str = None) -> dict:
        if not code or not code.strip():
            return {"command": "python", "stdout": "", "elapsed": 0,
                    "stderr": "Empty code", "returncode": -1, "success": False, "cancelled": False}
        cleaned = self._clean_code(code)
        ok, err = self._validate_syntax(cleaned)
        if not ok:
            return {"command": "python", "stdout": "", "elapsed": 0,
                    "stderr": f"SyntaxError: {err}", "returncode": -1,
                    "success": False, "cancelled": False}
        return self._install_and_run(cleaned, cwd=cwd)

# ══════════════════════════════════════════════════════════════
# SECTION 8 — JSON EXTRACTOR
# ══════════════════════════════════════════════════════════════

def extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            pass
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except Exception:
            text = fence_match.group(1).strip()
    text = re.sub(r"```[a-zA-Z0-9]*\s*", "", text)
    text = re.sub(r"```", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    first = text.find("{")
    if first != -1:
        text = text[first:]
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", text))
        except Exception:
            pass
        fragment = re.sub(r',?\s*"[^"]*$', "", text)
        fragment = re.sub(r",\s*$", "", fragment)
        fragment = re.sub(r",\s*([}\]])", r"\1", fragment)
        opens_b  = fragment.count("{") - fragment.count("}")
        opens_a  = fragment.count("[") - fragment.count("]")
        closed   = fragment + "]" * max(0, opens_a) + "}" * max(0, opens_b)
        try:
            return json.loads(closed)
        except Exception:
            pass
    return None

# ══════════════════════════════════════════════════════════════
# SECTION 8.5 — MCP CLIENT (stdio / subprocess transport)
# ══════════════════════════════════════════════════════════════

class MCPStdioClient:
    """
    Spawns an MCP server as a subprocess and communicates via
    stdin/stdout using JSON-RPC 2.0 (MCP stdio transport).

    Config entry format (Claude Desktop style):
    {
      "command": "/usr/bin/python3",
      "args":    ["/path/to/mcp_server.py", "--flag", "value"],
      "env":     {"KEY": "VALUE"}          ← optional
    }
    """

    def __init__(self, name: str, command: str, args: list, env: dict = None):
        self.name    = name
        self.command = command
        self.args    = args or []
        self.env     = env or {}
        self._proc:  Optional[subprocess.Popen] = None
        self._lock   = threading.Lock()
        self._req_id = 0
        self._tools_cache: Optional[list] = None

    # ── Process lifecycle ──────────────────────────────────
    def _start(self):
        if self._proc and self._proc.poll() is None:
            return  # Already running
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        cmd = [self.command] + self.args
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
            bufsize=0,
        )

    def _stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def __del__(self):
        try:
            self._stop()
        except Exception:
            pass

    # ── JSON-RPC over stdio ────────────────────────────────
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, method: str, params: dict = None, timeout: int = 30) -> dict:
        """Send a JSON-RPC request and read the response."""
        with self._lock:
            self._start()
            if not self._proc:
                raise RuntimeError(f"[MCP/{self.name}] Could not start server process")

            req_id  = self._next_id()
            payload = {
                "jsonrpc": "2.0",
                "id":      req_id,
                "method":  method,
                "params":  params or {},
            }
            line = json.dumps(payload) + "\n"
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()
            except BrokenPipeError:
                # Server died — restart once
                self._stop()
                self._start()
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()

            # Read lines until we find our response id
            deadline = time.time() + timeout
            while time.time() < deadline:
                # Check process is alive
                if self._proc.poll() is not None:
                    stderr_out = ""
                    try:
                        stderr_out = self._proc.stderr.read(2000).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"[MCP/{self.name}] Server process exited (code {self._proc.returncode}).\n"
                        f"stderr: {stderr_out[:400]}"
                    )

                # Non-blocking read with a short timeout
                rlist, _, _ = select.select([self._proc.stdout], [], [], 0.2)
                if not rlist:
                    continue

                raw_line = self._proc.stdout.readline()
                if not raw_line:
                    continue
                raw_line = raw_line.decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue

                try:
                    msg = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue  # Skip non-JSON lines (e.g., startup banners)

                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"[MCP/{self.name}] RPC error: {msg['error']}")
                    return msg.get("result", {})

            raise TimeoutError(f"[MCP/{self.name}] No response within {timeout}s for '{method}'")

    def _notify(self, method: str, params: dict = None):
        """Fire-and-forget notification (no id, no response expected)."""
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                return
            payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            try:
                self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except Exception:
                pass

    # ── MCP protocol methods ───────────────────────────────
    def initialize(self) -> dict:
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo":      {"name": "hackers-ai", "version": VERSION},
            "capabilities":    {},
        }, timeout=15)
        self._notify("notifications/initialized")
        return result

    def ping(self) -> bool:
        try:
            self._start()
            self.initialize()
            return True
        except Exception:
            return False
        finally:
            pass  # Keep process alive for subsequent calls

    def list_tools(self, force_refresh: bool = False) -> list:
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache
        try:
            result = self._send("tools/list", {})
            tools  = result.get("tools", [])
            self._tools_cache = tools
            return tools
        except Exception:
            return []

    def call_tool(self, tool_name: str, arguments: dict = None, timeout: int = 60) -> dict:
        return self._send(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
            timeout=timeout,
        )

    def list_resources(self) -> list:
        try:
            return self._send("resources/list", {}).get("resources", [])
        except Exception:
            return []

    def read_resource(self, uri: str) -> str:
        result   = self._send("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        return "\n".join(
            item.get("text", "")
            for item in contents
            if isinstance(item, dict) and item.get("type") == "text"
        )

    def list_prompts(self) -> list:
        try:
            return self._send("prompts/list", {}).get("prompts", [])
        except Exception:
            return []

    def get_prompt(self, name: str, arguments: dict = None) -> str:
        result   = self._send("prompts/get", {"name": name, "arguments": arguments or {}})
        messages = result.get("messages", [])
        parts    = []
        for msg in messages:
            content = msg.get("content", {})
            if isinstance(content, dict) and content.get("type") == "text":
                parts.append(content.get("text", ""))
            elif isinstance(content, str):
                parts.append(content)
        return "\n".join(parts)

    def format_tools_for_prompt(self) -> str:
        tools = self.list_tools()
        if not tools:
            return ""
        lines = [f"MCP Server [{self.name}] — {len(tools)} tools:"]
        for t in tools:
            params = ""
            schema = t.get("inputSchema", {})
            if isinstance(schema, dict):
                props  = schema.get("properties", {})
                params = ", ".join(props.keys()) if props else ""
            desc = t.get("description", "")[:70]
            lines.append(f"  {t['name']}({params}) — {desc}")
        return "\n".join(lines)

    @staticmethod
    def extract_text_result(result: dict) -> str:
        if not result:
            return ""
        contents = result.get("content", [])
        if isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") == "resource":
                        res = item.get("resource", {})
                        parts.append(res.get("text", str(res)))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(contents) if contents else ""


def _mcp_client_from_config(name: str, cfg: dict) -> MCPStdioClient:
    """Build an MCPStdioClient from a config dict entry."""
    return MCPStdioClient(
        name    = name,
        command = cfg.get("command", ""),
        args    = cfg.get("args", []),
        env     = cfg.get("env", {}),
    )

# ══════════════════════════════════════════════════════════════
# SECTION 9 — TOOL INSTALLER
# ══════════════════════════════════════════════════════════════

class ToolInstaller:
    _PIP_PACKAGES = {
        "sublist3r": "sublist3r", "theharvester": "theHarvester",
        "sqlmap": "sqlmap", "dirsearch": "dirsearch", "xsstrike": "xsstrike",
        "arjun": "arjun", "wfuzz": "wfuzz", "wafw00f": "wafw00f",
        "dnsrecon": "dnsrecon", "impacket": "impacket",
        "crackmapexec": "crackmapexec", "netexec": "netexec",
        "dnspython": "dnspython", "scapy": "scapy", "paramiko": "paramiko",
        "shodan": "shodan", "pwntools": "pwntools", "maigret": "maigret",
        "holehe": "holehe", "hashid": "hashid",
    }
    _GO_PACKAGES = {
        "subfinder":   "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        "httpx":       "github.com/projectdiscovery/httpx/cmd/httpx@latest",
        "dnsx":        "github.com/projectdiscovery/dnsx/cmd/dnsx@latest",
        "naabu":       "github.com/projectdiscovery/naabu/v2/cmd/naabu@latest",
        "nuclei":      "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
        "katana":      "github.com/projectdiscovery/katana/cmd/katana@latest",
        "ffuf":        "github.com/ffuf/ffuf/v2@latest",
        "gobuster":    "github.com/OJ/gobuster/v3@latest",
        "dalfox":      "github.com/hahwul/dalfox/v2@latest",
    }

    def __init__(self, executor: "CommandExecutor"):
        self.executor = executor

    def _shell(self, cmd: str) -> bool:
        ret = os.system(cmd + " 2>/dev/null")
        return ret == 0

    def install(self, tool: str) -> tuple:
        print(c("yellow", f"\n  ╭─ Installing: {c('white', tool)}"))
        self._shell("apt-get update -qq 2>/dev/null")
        ok = self._shell(f"apt-get install -y {shlex.quote(tool)}")
        if ok or shutil.which(tool):
            print(c("green", f"  ╰─ ✓ {tool} via apt"))
            return True, "apt", ""
        pip_pkg = self._PIP_PACKAGES.get(tool.lower(), tool)
        ok = self._shell(
            f"pip install {shlex.quote(pip_pkg)} --break-system-packages --ignore-installed -q"
        )
        if ok or shutil.which(tool):
            print(c("green", f"  ╰─ ✓ {tool} via pip"))
            return True, "pip", ""
        go_pkg = self._GO_PACKAGES.get(tool.lower())
        if go_pkg and shutil.which("go"):
            ok = self._shell(
                f"go install {go_pkg} && "
                f"cp ~/go/bin/{shlex.quote(tool)} /usr/local/bin/ 2>/dev/null; true"
            )
            if shutil.which(tool):
                print(c("green", f"  ╰─ ✓ {tool} via go"))
                return True, "go", ""
        print(c("red", f"  ╰─ ✗ Could not install {tool}"))
        return False, "none", ""

# ══════════════════════════════════════════════════════════════
# SECTION 10 — PYTHON FALLBACK
# ══════════════════════════════════════════════════════════════

class PythonFallback:
    SYSTEM_CTX = textwrap.dedent("""
You are an expert Python developer writing scripts for a Linux agent.
A command-line task failed completely. Write a Python 3 script to accomplish the same task.

Rules:
1. Output ONLY raw Python3 code — with markdown fences, no explanation eg. ```python ```
2. Use ONLY spaces (4 per indent level) — NEVER tabs
3. Auto-install required libs at the top:
   import subprocess, sys
   subprocess.run([sys.executable,"-m","pip","install","<lib>","--quiet","--break-system-packages"],check=False)
4. Fully self-contained, no arguments needed
5. Print all results clearly to stdout
6. Handle errors with try/except
    """).strip()

    def __init__(self, model: str, py_exec: "PythonExecutor", cmd_exec: "CommandExecutor"):
        self.model    = model
        self.py_exec  = py_exec
        self.cmd_exec = cmd_exec

    def generate_and_run(self, failed_cmd: str, task_desc: str,
                         error_output: str, cwd: str = None) -> dict:
        print(c("magenta", "\n  [PythonFallback] Generating Python replacement..."))
        prompt = (
            self.SYSTEM_CTX + "\n\n"
            f"Failed command: {failed_cmd}\n"
            f"Task: {task_desc}\n"
            f"Error: {error_output[:600]}\n\n"
            "Write the Python 3 script now inside a ```python ... ``` fence, nothing else:"
        )
        try:
            agent   = FreeLLM(model=self.model)
            raw     = agent.ask(prompt).strip()
            code    = PythonExecutor._clean_code(raw)
            if not code or len(code) < 20:
                return {"success": False, "stdout": "", "stderr": "Empty fallback script",
                        "returncode": -1, "elapsed": 0, "cancelled": False, "command": failed_cmd}
            ok, err = PythonExecutor._validate_syntax(code)
            if not ok:
                return {"success": False, "stdout": "", "stderr": f"Syntax error: {err}",
                        "returncode": -1, "elapsed": 0, "cancelled": False, "command": failed_cmd}
            result = self.py_exec.run(code, cwd=cwd)
            print(c("green" if result["success"] else "red",
                    "  [PythonFallback] " + ("✓ Succeeded" if result["success"] else "✗ Failed")))
            return result
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e),
                    "returncode": -1, "elapsed": 0, "cancelled": False, "command": failed_cmd}

# ══════════════════════════════════════════════════════════════
# SECTION 11 — ERROR ANALYZER
# ══════════════════════════════════════════════════════════════

class ErrorAnalyzer:
    def __init__(self, model: str, executor: "CommandExecutor"):
        self.model     = model
        self.executor  = executor
        self.installer = ToolInstaller(executor)
        self.inspector = ToolInspector()

    def analyze_and_fix(self, failed_cmd: str, error_output: str, history: list) -> Optional[str]:
        parts     = failed_cmd.strip().split()
        tool      = parts[0] if parts else ""
        help_text = ""
        if tool and shutil.which(tool):
            help_text = self.inspector.get_help(tool, max_chars=1500)
        fix_prompt = (
            "A Linux command failed. Return ONLY this JSON, nothing else:\n"
            '```json {"fixed_command": "<corrected single-line shell command>"}```\n\n'
            f"Failed:\n{failed_cmd}\n\n"
            f"Error:\n{error_output[:600]}\n"
            + (f"\nTool --help output:\n{help_text}\n" if help_text else "")
            + "\nCRITICAL: Use ONLY flags that appear in the help output above."
        )
        try:
            agent  = FreeLLM(model=self.model)
            raw    = agent.ask(fix_prompt).strip()
            result = extract_json(raw)
            if result and result.get("fixed_command"):
                fixed = result["fixed_command"].strip().strip("`")
                return fixed if fixed and fixed != failed_cmd.strip() else None
        except Exception:
            return None

    def suggest_alternative(self, failed_cmd: str, task_desc: str,
                            error_output: str) -> Optional[str]:
        print(c("yellow", "  [Analyzer] Finding alternative approach..."))
        prompt = (
            "A Linux command failed after multiple retries. "
            "Return ONLY this JSON, nothing else:\n"
            '{"alternative_command": "<single-line shell command using a DIFFERENT tool>",'
            ' "reason": "<why this alternative works>"}\n\n'
            f"Failed: {failed_cmd}\n"
            f"Task: {task_desc}\n"
            f"Error: {error_output[:500]}\n\n"
            "Rules:\n"
            "- Use a DIFFERENT tool than the failed one\n"
            "- Must accomplish the exact same goal\n"
            "- If tool might be missing, prefix: apt-get install -y <tool> && <command>"
        )
        try:
            agent  = FreeLLM(model=self.model)
            raw    = agent.ask(prompt).strip()
            result = extract_json(raw)
            if result and result.get("alternative_command"):
                alt = result["alternative_command"].strip().strip("`")
                if alt and alt != failed_cmd:
                    print(c("cyan", f"  [Analyzer] Alternative: {alt[:80]}"))
                    return alt
        except Exception:
            pass
        return None

# ══════════════════════════════════════════════════════════════
# SECTION 12 — UNIFIED QUERY ANALYZER
# Replaces separate IntentClassifier + ContextResolver with a
# single LLM call that handles intent, context, and history together.
# ══════════════════════════════════════════════════════════════

class QueryAnalyzer:
    """
    One JSON call that decides:
      - intent   : "task" | "informational"
      - ready    : true if all context needed is available (from input OR history)
      - enriched : full task string with history values substituted in
      - question : if ready=false, the ONE question to ask the user

    Handles the "is port 80 open" case after pinging 192.168.0.1 correctly:
    history has the IP → intent=task, ready=true, enriched="check if port 80 open on 192.168.0.1"
    """

    SYSTEM_PROMPT = textwrap.dedent("""
You are the query analysis module for a Linux/pentesting AI agent.
Analyse the CURRENT INPUT and respond with ONLY a ```json ... ``` block — nothing else.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — INTENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"task"          → requires running a shell command / doing something on the system
                  (scan, ping, check port, open app, create file, install, recon…)
                  INCLUDES questions about system/network state:
                    "is port 80 open?"  "is 192.168.0.1 alive?"  "check if ssh runs"
                  When in doubt → "task"

"informational" → pure knowledge question, zero system action needed
                  ONLY: "what is nmap", "how does XSS work", "explain TCP handshake"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — CONTEXT (only when intent="task")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Check whether the task has all the information needed to execute it.

HISTORY RULE (critical):
  If the current input is vague or refers to a previous target
  ("it", "that", "the target", "same IP", "that host", or simply omits a target),
  scan the HISTORY for the most recent IP / domain / URL / hostname and
  substitute it into the enriched_task. Set found_in="history".

ALWAYS ready=true for:
  - local system tasks (disk, cpu, memory, processes, files, services)
  - tasks whose input already contains an IP / domain / URL / port number
  - follow-up tasks clearly connected to recent history (even if terse)

ready=false ONLY when:
  - task obviously needs a remote target AND none exists anywhere in history

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT — respond ONLY with:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```json
{
  "intent": "task",
  "ready": true,
  "found_in": "task|history|local|fallback",
  "enriched_task": "<complete task string with all values filled in>",
  "question": null
}
```
OR when more info is genuinely needed:
```json
{
  "intent": "task",
  "ready": false,
  "found_in": "none",
  "enriched_task": null,
  "question": "<one short specific question>"
}
```
OR for pure knowledge questions:
```json
{
  "intent": "informational",
  "ready": true,
  "found_in": "task",
  "enriched_task": "<the question as-is>",
  "question": null
}
```
    """).strip()

    # Fast-path regex: these patterns are ALWAYS "task" — no LLM call needed
    _TASK_RE = re.compile(
        r'\b(scan|nmap|ping|curl|wget|ssh|ftp|run|exec|start|stop|restart|'
        r'install|uninstall|remove|create|delete|mkdir|rm\b|mv\b|cp\b|cat\b|'
        r'ls\b|find\b|check|test|is\s+port|port\s+\d+|enumerate|brute|fuzz|'
        r'recon|exploit|crack|hash|connect|listen|upload|download|deploy|kill\b|'
        r'ps\b|top\b|df\b|du\b|grep\b|awk\b|sed\b|chmod|chown|ln\b|tar\b|'
        r'zip\b|unzip|netstat|ss\b|ip\b|ifconfig|traceroute|dig\b|host\b|'
        r'nslookup|hydra|gobuster|ffuf|sqlmap|nikto|nuclei|open\s+port|'
        r'show\s+(me\s+)?(files?|ports?|process|service|disk|memory|cpu|network|ip))\b',
        re.IGNORECASE
    )

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model           = model
        self._question_count = 0
        self._max_questions  = 2

    def analyze(self, user_input: str, history: list) -> dict:
        """
        Returns dict with keys: intent, ready, found_in, enriched_task, question
        """
        _fallback_task = {
            "intent": "task", "ready": True, "found_in": "fallback",
            "enriched_task": user_input, "question": None,
        }

        # ── Fast path: obvious task patterns ──────────────────────────────
        if self._TASK_RE.search(user_input):
            # Still need to resolve context (history fill), so fall through to LLM
            # but prime it with intent=task knowledge
            pass

        # ── Question limit guard ──────────────────────────────────────────
        if self._question_count >= self._max_questions:
            self._question_count = 0
            return _fallback_task

        # ── Build history block ───────────────────────────────────────────
        history_lines = []
        for h in reversed(history[-10:]):
            prefix  = "USER" if h["role"] == "user" else "AI"
            snippet = h["content"][:500].replace("\n", " ")
            history_lines.append(f"[{prefix}]: {snippet}")
        history_block = "\n".join(history_lines) if history_lines else "(none)"

        prompt = (
            self.SYSTEM_PROMPT + "\n\n"
            f"CONVERSATION HISTORY (newest first):\n{history_block}\n\n"
            f"CURRENT INPUT: {user_input}"
        )

        try:
            agent  = FreeLLM(model=self.model)
            raw    = agent.ask(prompt)
            result = extract_json(raw)
            if result and "intent" in result and "ready" in result:
                if not result.get("ready"):
                    self._question_count += 1
                else:
                    self._question_count = 0
                    if not result.get("enriched_task"):
                        result["enriched_task"] = user_input
                return result
        except Exception:
            pass
        return _fallback_task

# ══════════════════════════════════════════════════════════════
# SECTION 13 — PLANNER ENGINE
# ══════════════════════════════════════════════════════════════

class PlannerEngine:
    CURL_XSS_PAYLOADS = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg/onload=alert(1)>",
        "\" onmouseover=alert(1) x=\"",
        "'><script>alert(1)</script>",
        "</tag><script>alert(1)</script>",
        "javascript:alert(1)",
        "<iframe src=javascript:alert(1)>",
        "%3Cscript%3Ealert(1)%3C/script%3E",
        "<ScRiPt>alert(1)</ScRiPt>",
        "<script>alert`1`</script>",
        "'-alert(1)-'",
        "\"><img src=x onerror=alert(1)>",
        "<body onload=alert(1)>",
        "<details open ontoggle=alert(1)>",
    ]

    def plan_next(self, original_task: str, completed_steps: list,
                  profile: dict, model: str = DEFAULT_MODEL) -> Optional[dict]:
        completed_str = "\n".join(
            f"Step {s['id']} — {s['desc']}:\n{s['output'][:800]}"
            for s in completed_steps
        )
        available_tools = profile.get("available_tools", [])
        mcp_active      = bool(profile.get("active_mcp_tools", ""))
        if mcp_active:
            tool_help = ""
            wordlists = {}
        else:
            relevant  = self._detect_relevant_tools(original_task, available_tools)
            tool_help = self._fetch_tools_help(relevant)
            wordlists = WordlistFinder().scan()
        system_ctx      = self._build_system_ctx(profile, tool_help, wordlists)
        system_ctx     += tool_help
        mcp_reminder = (
            "IMPORTANT: ALL steps MUST be type=mcp_call. "
            "Use ONLY tool names from MCP TOOL CATALOGUE. "
            "Match EXACT parameter names from the schema. "
        ) if profile.get("active_mcp_tools") else ""
        prompt = (
            system_ctx + "\n\n"
            f"ORIGINAL TASK: {original_task}\n\n"
            f"COMPLETED STEPS AND THEIR REAL OUTPUT:\n{completed_str}\n\n"
            "Based on the actual output above, generate ONLY the next required steps.\n"
            "Use the real output values directly in commands — do not re-run completed steps.\n"
            "If the task is fully complete, return an empty steps array: {\"steps\": []}\n"
            f"{mcp_reminder}"
            "RESPOND WITH ONLY A ```json ... ``` fenced block."
        )
        for attempt in range(1, 4):
            try:
                agent = FreeLLM(model=model)
                raw   = agent.ask(prompt)
                parsed_check = extract_json(raw)
                if parsed_check and "ready" in parsed_check and "steps" not in parsed_check:
                    prompt += "\n\n[ERROR]: Return a plan with steps array, not a context response."
                    continue
                plan = extract_json(raw)
                if plan is not None and isinstance(plan.get("steps"), list):
                    mcp_active = bool(profile.get("active_mcp_tools", ""))
                    filtered = []
                    for step in plan["steps"]:
                        stype      = step.get("type", "command")
                        desc_lower = (step.get("description") or "").lower()
                        # In MCP mode: convert command/python steps or drop them
                        if mcp_active and stype not in ("mcp_call", "info"):
                            exec_tool = profile.get("active_mcp_exec_tool", "")
                            if stype == "command" and exec_tool:
                                # Rewrite as mcp_call → execute_command
                                step["type"] = "mcp_call"
                                step["args"] = {"command": step.get("command", "")}
                                step["tool"] = exec_tool
                                step["command"] = ""
                                print(c("dim", f"  [Planner] MCP mode — rewrote command→{exec_tool}: {desc_lower[:50]}"))
                            else:
                                print(c("dim", f"  [Planner] MCP mode — dropped {stype} step: {desc_lower[:55]}"))
                                continue
                        FORBIDDEN_DESCS = [
                            "analyze result", "analyse result",
                            "summarize result", "summarise result",
                            "review result", "check result",
                            "generate report from result",
                        ]
                        if stype == "python" and any(
                            desc_lower == phrase or desc_lower.startswith(phrase)
                            for phrase in FORBIDDEN_DESCS
                        ):
                            continue
                        if stype == "python":
                            if not step.get("description"):
                                step["description"] = original_task
                            step["command"] = ""
                        filtered.append(step)
                    plan["steps"] = filtered
                    return plan
            except Exception as e:
                print(c("red", f"  [Planner] plan_next attempt {attempt} error: {e}"))
        return None

    def _fetch_tools_help(self, tools_needed: list) -> str:
        inspector = ToolInspector()
        parts = []
        for tool in tools_needed:
            if not shutil.which(tool):
                continue
            help_text = inspector.get_help(tool, max_chars=1800)
            if help_text:
                parts.append(f"### {tool} (use ONLY these flags)\n{help_text}")
        if not parts:
            return ""
        return (
            "\n\n═══ TOOL HELP — READ BEFORE GENERATING COMMANDS ═══\n"
            "These are the EXACT available flags. Do not invent flags not shown here.\n\n"
            + "\n\n".join(parts)
        )

    def _detect_relevant_tools(self, user_input: str, available: list) -> list:
        lower = user_input.lower()
        SIMPLE_OPS = [
            "delete", "remove", "rm ", "mkdir", "copy", "cp ", "move",
            "mv ", "rename", "touch", "chmod", "chown", "ln ", "cat ",
            "ls ", "list files", "list dir", "show files",
        ]
        if any(op in lower for op in SIMPLE_OPS):
            PENTEST = ["scan", "xss", "sql", "inject", "fuzz", "brute",
                       "exploit", "recon", "enum", "crack", "hash"]
            if not any(p in lower for p in PENTEST):
                return []
        relevant = []
        web_keywords = ["xss", "sql", "inject", "http", "web", "url", "fuzz",
                        "scan", "recon", "dir", "path", "param", "test"]
        if any(k in lower for k in web_keywords):
            relevant += ["curl", "wget"]
        tool_map = {
            "nmap":          ["port", "scan", "service", "version", "network", "host"],
            "ffuf":          ["fuzz", "dir", "path", "brute", "word"],
            "gobuster":      ["dir", "path", "brute", "word", "vhost"],
            "feroxbuster":   ["dir", "path", "brute"],
            "dalfox":        ["xss"],
            "xsstrike":      ["xss"],
            "sqlmap":        ["sql", "inject", "sqli", "database"],
            "nikto":         ["nikto", "web scan", "vuln scan"],
            "nuclei":        ["nuclei", "template", "cve"],
            "whatweb":       ["tech", "fingerprint", "detect", "cms"],
            "wafw00f":       ["waf", "firewall"],
            "httpx-toolkit": ["live", "host", "http probe"],
            "hydra":         ["brute", "password", "login", "ssh", "ftp"],
            "john":          ["crack", "hash", "password"],
            "hashcat":       ["crack", "hash", "password"],
            "aircrack-ng":   ["wifi", "wireless", "wpa"],
            "wifite":        ["wifi", "wireless"],
            "msfconsole":    ["exploit", "metasploit", "msf"],
            "enum4linux":    ["smb", "windows", "samba", "netbios"],
            "smbmap":        ["smb", "windows", "samba", "share"],
            "ldapsearch":    ["ldap", "active directory", "ad"],
            "crackmapexec":  ["smb", "windows", "lateral"],
            "searchsploit":  ["exploit", "cve", "vulnerability"],
            "binwalk":       ["firmware", "binary", "extract"],
            "radare2":       ["reverse", "binary", "asm", "disassemble"],
            "volatility3":   ["forensic", "memory", "dump"],
            "steghide":      ["steganography", "steg", "image"],
            "wpscan":        ["wordpress", "wp"],
            "joomscan":      ["joomla"],
            "subfinder":     ["subdomain", "sub"],
            "amass":         ["subdomain", "osint", "recon"],
            "dnsrecon":      ["dns", "domain", "zone"],
            "sherlock":      ["osint", "username", "social"],
            "exiftool":      ["exif", "metadata"],
        }
        for tool, keywords in tool_map.items():
            if tool in available and any(k in lower for k in keywords):
                relevant.append(tool)
        for t in available:
            if re.search(rf'\b{re.escape(t.lower())}\b', lower):
                relevant.append(t)
        return list(dict.fromkeys(relevant))

    def _build_system_ctx(self, profile: dict, tool_help: str, wordlists: dict) -> str:
        tools_str    = ", ".join(profile.get("available_tools", [])) or "standard linux tools"
        wl_finder    = WordlistFinder()
        wordlist_str = wl_finder.format_for_prompt(wordlists)
        inspector  = ToolInspector()
        httpx_type = inspector.identify_httpx()
        httpx_note = ""
        if httpx_type == "scanner":
            httpx_note = (
                "\nNOTE: httpx on this system = projectdiscovery scanner "
                "(supports -title, -tech-detect, -status-code, -l <file>)"
            )
        elif httpx_type == "client":
            httpx_note = (
                "\nNOTE: httpx on this system = encode HTTP client (curl-like). "
                "Do NOT use scanner flags like -title or -tech-detect. "
                "Use curl instead for single-URL requests."
            )
        payloads_str = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(self.CURL_XSS_PAYLOADS))
        mcp_ctx        = profile.get("active_mcp_tools", "")
        mcp_name       = profile.get("active_mcp_name", "")
        mcp_schema_ctx = profile.get("active_mcp_schema", "")

        # ── Build the MCP block — dominant priority when server is active ──
        mcp_has_exec  = profile.get("active_mcp_has_exec", False)
        mcp_exec_tool = profile.get("active_mcp_exec_tool", "execute_command")
        if mcp_ctx:
            exec_note = (
                f"\n  execute_command fallback: If no dedicated tool exists for a task, use:\n"
                f"    {{{{\"type\": \"mcp_call\", \"tool\": \"{mcp_exec_tool}\", "
                f"\"args\": {{{{\"command\": \"<shell command>\"}}}}}}}}"
            ) if mcp_has_exec else ""
            mcp_block = textwrap.dedent(f"""
╔══════════════════════════════════════════════════════════════╗
║  ACTIVE MCP SERVER: {mcp_name:<40} ║
║  ALL steps must be type=mcp_call using tools listed below.   ║
╚══════════════════════════════════════════════════════════════╝

MCP TOOL CATALOGUE (use these EXACT tool names and arg keys):
{mcp_schema_ctx}
{exec_note}

MCP STEP FORMAT:
  {{{{
    "id": <n>,
    "type": "mcp_call",
    "tool": "<exact_tool_name>",
    "args": {{{{"<param>": "<value>"}}}},
    "command": "",
    "description": "<what this does>",
    "depends_on": []
  }}}}

MCP RULES:
  1. ALWAYS use the most specific tool available (e.g. nmap_scan not execute_command for nmap).
  2. If no dedicated tool exists for what you need → use execute_command with the shell command.
  3. EVERY step must be type=mcp_call. Never type=command or type=python.
  4. Chain steps with depends_on when a step needs a prior step's output.
  5. For web testing: use nikto_scan, gobuster_scan, sqlmap_scan etc. directly.
     For custom curl/ffuf/wfuzz → use execute_command.
""").strip()
        else:
            mcp_block = ""

        # No web block for pure RE MCP (Ghidra).
        # For exec-capable MCP: give web testing guidance but route via MCP tools.
        if not mcp_ctx:
            web_block = textwrap.dedent(f"""
═══ WEB TESTING — MANDATORY PRIORITY ORDER ═══
ALWAYS follow this exact sequence for web/XSS/injection testing:

PHASE 1 — curl (ALWAYS start here, no exceptions):
  1a. Probe target: curl -si 'URL' | head -60
  1b. Test reflection with each of these payloads in order:
{payloads_str}
      Command format: curl -si 'URL?param=PAYLOAD' | grep -i 'PAYLOAD\\|script\\|alert\\|onerror'
  1c. Try URL-encoded variants if raw payloads are filtered
  1d. Check response headers too: look for CSP, X-XSS-Protection

PHASE 2 — Only if curl confirms reflection but can't confirm execution:
  Use dalfox: dalfox url 'URL'

PHASE 3 — Only if dalfox insufficient:
  Use xsstrike: python3 /path/to/xsstrike.py -u 'URL'

PHASE 4 — Template scan for known CVEs:
  Use nuclei with xss templates

RULE: NEVER skip Phase 1. NEVER add a step just to "analyze results" —
      the agent summarizes automatically after execution.
""").strip()
        elif mcp_has_exec:
            # exec-capable MCP — guide web testing but all via MCP tools
            web_block = textwrap.dedent(f"""
═══ WEB TESTING VIA MCP ═══
Use dedicated MCP tools first, then execute_command for anything else:

  nikto_scan(target, additional_args)           — web vuln scan
  gobuster_scan(url, mode, wordlist)             — dir/vhost brute
  sqlmap_scan(url, data, additional_args)        — SQL injection
  wpscan_analyze(url, additional_args)           — WordPress scan
  execute_command(command="curl -si 'URL'...")   — custom HTTP probes
  execute_command(command="ffuf -u 'URL/FUZZ'...")— fuzzing
  execute_command(command="nikto -h URL")        — when nikto_scan unavailable

PATH TRAVERSAL payloads to try via execute_command:
  curl -si 'URL?param=../../../../etc/passwd'
  curl -si 'URL?param=..%2F..%2F..%2Fetc%2Fpasswd'
  curl -si 'URL?param=....//....//etc/passwd'
  curl -si 'URL/static/../../../etc/passwd'
""").strip()
        else:
            web_block = ""

        mcp_type_note = (
            '\n12. mcp_call steps: type="mcp_call", tool="<name>", args={{...}}, command="" '
            '(command must be empty string for mcp_call)'
        ) if mcp_ctx else ""

        # Generic example using first real tool name from the server
        _first_tool = ""
        _first_args = "{}"
        if mcp_ctx and mcp_schema_ctx:
            for _ln in mcp_schema_ctx.splitlines():
                _ln = _ln.strip()
                if _ln.startswith("  ") and "(" in _ln and not _ln.startswith("→"):
                    _first_tool = _ln.strip().split("(")[0].strip()
                    break
        mcp_output_example = (
            textwrap.dedent(f"""
    Example mcp_call step (use EXACT tool names from catalogue above):
    {{{{{{{{
      "id": 2,
      "type": "mcp_call",
      "tool": "{_first_tool or '<tool_name>'}",
      "args": {{{{{{"<param>": "<value>"}}}}}}}},
      "command": "",
      "description": "What this step does",
      "depends_on": [1]
    }}}}}}}}
""") if mcp_ctx else ""
        )

        return textwrap.dedent(f"""
You are Hackers AI — an autonomous Linux agent with full shell access.

LIVE SYSTEM:
  Distro   : {profile.get('distro','Linux')}
  Kernel   : {profile.get('kernel','')}
  Arch     : {profile.get('arch','')}
  Hostname : {profile.get('hostname','')}
  User     : {profile.get('whoami','')} | Root: {profile.get('root',False)}
  CWD      : {profile.get('cwd','/root')}
  HOME     : {profile.get('real_home','/root')}
  Target   : {profile.get('sticky_target','(none set)')}
  Shell tools : {tools_str}
{httpx_note}

{mcp_block}

{wordlist_str}

{web_block}

═══ COMMAND GENERATION RULES ═══
1. Output ONLY a ```json ... ``` fenced block — absolutely nothing else
2. NEVER invent tool flags — use ONLY what appears in TOOL HELP section below
3. NEVER use sudo — agent is already root
4. ALL URLs and file paths MUST be single-quoted in shell commands
5. pip installs: always add --break-system-packages --quiet
6. Installation: apt-get install -y <tool>
7. python type steps: type="python", command="" (empty), description="what to do"
8. FORBIDDEN steps: type=python for "analyze","summarize","report","check results"
   These are handled automatically — NEVER add them.
9. depends_on: [] for independent steps, [id] only if step needs prior output
10. For wordlists: use ONLY paths from AVAILABLE WORDLISTS section above
11. ADAPTIVE steps: if a step's command depends on the RESULT of a previous
    step (not just a file), make it type="python" so it can subprocess the
    prior result and decide what to run.{mcp_type_note}

═══ OUTPUT FORMAT — CRITICAL ═══
YOUR ENTIRE RESPONSE MUST BE EXACTLY THIS — NOTHING ELSE:
{mcp_output_example}
```json
{{{{
  "intent": "task",
  "summary": "<one-line summary>",
  "requires_root": false,
  "warning": null,
  "steps": [
    {{{{
      "id": 1,
      "type": "command|python|mcp_call",
      "tool": "<tool name, or null for command/python>",
      "args": {{{{}}}},
      "command": "<shell command — empty string for mcp_call and python>",
      "description": "<what this step does>",
      "depends_on": []
    }}}}
  ]
}}}}
```

type values: "command" | "python" | "mcp_call" | "info"
warning: null or string (not the string "null")
        """).strip()

    def plan(self, user_input: str, history: list, profile: dict,
             model: str = DEFAULT_MODEL) -> Optional[dict]:
        available_tools = profile.get("available_tools", [])
        mcp_active      = bool(profile.get("active_mcp_tools", ""))
        if mcp_active:
            # MCP is active — skip shell tool help entirely, schema already in profile
            tool_help = ""
            wordlists = {}
            print(c("dim", "  [Planner] MCP mode"))
        else:
            relevant  = self._detect_relevant_tools(user_input, available_tools)
            if relevant:
                print(c("dim", f"  [Planner] Fetching help for: {', '.join(relevant[:8])}..."), end="", flush=True)
            tool_help = self._fetch_tools_help(relevant)
            wordlists = WordlistFinder().scan()
            if relevant:
                print(c("green", " done"))
        system_ctx  = self._build_system_ctx(profile, tool_help, wordlists)
        system_ctx += tool_help
        base_parts = [system_ctx, ""]
        for h in history[-2:]:
            prefix  = "USER" if h["role"] == "user" else "ASSISTANT"
            content = h["content"][:200] if h["role"] == "assistant" else h["content"]
            base_parts.append(f"[{prefix}]: {content}")
        if profile.get("active_mcp_tools"):
            base_parts.append(
                f"[NEW TASK]: {user_input}\n"
                "IMPORTANT: ALL steps MUST be type=mcp_call. "
                "Use ONLY tool names from the MCP TOOL CATALOGUE above. "
                "Put actual arg values in the args dict — match EXACT parameter names. "
                "RESPOND WITH ONLY A ```json ... ``` FENCED BLOCK — NOTHING ELSE."
            )
        else:
            base_parts.append(
                f"[NEW TASK]: {user_input}\n"
                "Remember: python steps use type=python, command=empty string. "
                "No analysis/summarize steps. Single-quote all URLs. "
                "RESPOND WITH ONLY A ```json ... ``` FENCED BLOCK — NOTHING ELSE."
            )
        prompt = "\n".join(base_parts)
        for attempt in range(1, 4):
            try:
                agent = FreeLLM(model=model)
                raw   = agent.ask(prompt)
                plan  = extract_json(raw)
                if plan and isinstance(plan.get("steps"), list) and plan["steps"]:
                    mcp_active = bool(profile.get("active_mcp_tools", ""))
                    filtered = []
                    for step in plan["steps"]:
                        stype      = step.get("type", "command")
                        desc_lower = (step.get("description") or "").lower()
                        # In MCP mode: convert command/python steps or drop them
                        if mcp_active and stype not in ("mcp_call", "info"):
                            exec_tool = profile.get("active_mcp_exec_tool", "")
                            if stype == "command" and exec_tool:
                                # Rewrite as mcp_call → execute_command
                                step["type"] = "mcp_call"
                                step["args"] = {"command": step.get("command", "")}
                                step["tool"] = exec_tool
                                step["command"] = ""
                                print(c("dim", f"  [Planner] MCP mode — rewrote command→{exec_tool}: {desc_lower[:50]}"))
                            else:
                                print(c("dim", f"  [Planner] MCP mode — dropped {stype} step: {desc_lower[:55]}"))
                                continue
                        FORBIDDEN_DESCS = [
                            "analyze result", "analyse result",
                            "summarize result", "summarise result",
                            "review result", "check result",
                            "generate report from result",
                        ]
                        if stype == "python" and any(
                            desc_lower == phrase or desc_lower.startswith(phrase)
                            for phrase in FORBIDDEN_DESCS
                        ):
                            print(c("dim", f"  [Planner] Dropped forbidden python step: {desc_lower[:60]}"))
                            continue
                        if stype == "python":
                            if not step.get("description"):
                                step["description"] = step.get("command", user_input)
                            step["command"] = ""
                        filtered.append(step)
                    if filtered:
                        plan["steps"] = filtered
                        return plan
                prompt += (
                    "\n\n[REMINDER]: Previous response was NOT valid JSON wrapped in ```json ... ```. "
                    "Your ENTIRE response must be ONLY a ```json ... ``` fenced block. "
                    "Start with ```json, end with ```, nothing outside those fences. "
                    "Single-quote all URLs in commands."
                )
                if attempt > 1:
                    print(c("yellow", f"  [Planner] Attempt {attempt}/3 — invalid JSON, retrying..."))
            except Exception as e:
                print(c("red", f"  [Planner] Attempt {attempt} error: {e}"))
        return None

# ══════════════════════════════════════════════════════════════
# SECTION 14 — RESPONSE GENERATOR
# ══════════════════════════════════════════════════════════════

class ResponseGenerator:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    def ask(self, user_input: str, history: list, profile: dict) -> str:
        tools_str = ", ".join(profile.get("available_tools", [])[:30])
        system_ctx = textwrap.dedent(f"""
You are Hackers AI — a powerful Linux agent assistant.

SYSTEM:
  OS: {profile.get('distro','Linux')} | Kernel: {profile.get('kernel','')}
  Host: {profile.get('hostname','')} | IP: {profile.get('ip','')} | Root: True
  CWD: {profile.get('cwd','/root')}
  Target: {profile.get('sticky_target','(none)')}
  Tools: {tools_str}

RULES:
- Be SHORT and direct — no fluff
- NEVER use markdown: no ``` fences, no ** bold, no # headers
- Show commands as plain text on their own line, e.g.:  nc -zv 192.168.0.1 80
- Reference CWD and target when relevant
        """).strip()
        parts = [system_ctx, "\n--- RECENT CONVERSATION ---"]
        for h in history[-4:]:
            prefix  = "USER" if h["role"] == "user" else "AI"
            content = h["content"][:300].replace("\n", " ")
            parts.append(f"{prefix}: {content}")
        parts.append(f"\nUSER: {user_input}\nAI:")
        agent    = FreeLLM(model=self.model)
        response = agent.ask("\n".join(parts))
        response = re.sub(r"^\s*USER:.*?\n", "", response, flags=re.IGNORECASE).strip()
        # Strip any markdown code fences the model may still emit
        response = re.sub(r"```[a-zA-Z]*\n?", "", response)
        response = re.sub(r"```", "", response)
        response = re.sub(r"\*\*([^*]+)\*\*", r"\1", response)  # **bold** → plain
        response = re.sub(r"^#{1,6}\s+", "", response, flags=re.MULTILINE)  # ## headers
        # Strip shell prompt artifacts the LLM sometimes prepends (e.g. kali㉿kali)-[~]$)
        response = re.sub(r"^\s*\([^)]*㉿[^)]*\)-\[.*?\]\$\s*", "", response, flags=re.MULTILINE)
        response = re.sub(r"^\s*root@\S+:[^\$#]*[#\$]\s*", "", response, flags=re.MULTILINE)
        return response.strip()

# ══════════════════════════════════════════════════════════════
# SECTION 15 — EXECUTION ENGINE
# ══════════════════════════════════════════════════════════════

class ExecutionEngine:
    def __init__(self, memory: MemoryDB, model: str = DEFAULT_MODEL):
        self.memory      = memory
        self.model       = model
        self.cmd_exec    = CommandExecutor()
        self.py_exec     = PythonExecutor()
        self.analyzer    = ErrorAnalyzer(model, self.cmd_exec)
        self._print_lock        = threading.Lock()
        self._abort             = False
        self._cwd               = None
        self._user_input        = ""
        self._mcp_client        = None   # set by CLI when mcp_call steps are needed
        self._current_step_args = {}     # args dict for current mcp_call step

    def _lprint(self, *args, **kwargs):
        with self._print_lock:
            print(*args, **kwargs)

    def _group_steps(self, steps: list) -> list:
        completed = set()
        remaining = list(steps)
        batches   = []
        while remaining:
            ready = [
                s for s in remaining
                if all(d in completed for d in (s.get("depends_on") or []))
            ]
            if not ready:
                ready = remaining[:]
            batches.append(ready)
            for s in ready:
                completed.add(s.get("id"))
                remaining.remove(s)
        return batches

    def execute_plan(self, plan: dict, task_id: str) -> str:
        steps       = [s for s in plan.get("steps", []) if s.get("type") != "info"]
        info_steps  = [s for s in plan.get("steps", []) if s.get("type") == "info"]
        results     = {}
        step_stdout = {}

        for s in info_steps:
            sid = s.get("id", "?")
            results[sid] = f"[Step {sid}] {s.get('description','')}"
            self.memory.log_task_step(task_id, sid, "", "", s.get("description",""), "info")

        batches = self._group_steps(steps)

        for batch_idx, batch in enumerate(batches):
            if self._abort:
                break
            if len(batch) == 1:
                step  = batch[0]
                sid   = step.get("id", "?")
                stype = step.get("type", "command")
                desc  = step.get("description", "")
                cmd   = step.get("command", "")
                self._lprint(c("cyan", f"\n  ▶ Step {sid}: ") + c("white", desc))
                if cmd or stype in ("python", "mcp_call"):
                    if stype == "python" and step_stdout:
                        prior = "\n".join(
                            f"[Step {k} output]:\n{v}"
                            for k, v in step_stdout.items()
                            if v.strip()
                        )
                        if prior:
                            desc = f"{desc}\n\nPRIOR STEP OUTPUTS (use these directly, do not re-run commands to get them):\n{prior}"
                    # Expose mcp args dict so _run_with_healing can read them
                    self._current_step_args = step.get("args") or {}
                    result = self._run_with_healing(
                        cmd, stype, task_id, sid, step.get("tool",""), desc=desc
                    )
                    if result.get("cancelled"):
                        if not self._ask_continue():
                            self._abort = True
                            break
                    step_stdout[sid] = result.get("stdout", "").strip()
                    out = result["stdout"][:2500] or result["stderr"][:500]
                    results[sid] = f"[Step {sid} — {desc}]\n$ {result['command']}\n{out}"
            else:
                self._lprint(c("magenta",
                    f"\n  ⚡ Running {len(batch)} steps in parallel (batch {batch_idx+1}/{len(batches)})"))
                for i, s in enumerate(batch):
                    conn = "├─" if i < len(batch)-1 else "└─"
                    self._lprint(c("dim", f"     {conn} [{s.get('id')}] {s.get('description','')[:65]}"))
                self._lprint(c("dim", "  " + "─"*62))

                threads       = []
                batch_results = {}
                lock          = self._print_lock

                def _worker(step_item, br=batch_results, lk=lock):
                    sid   = step_item.get("id", "?")
                    stype = step_item.get("type", "command")
                    desc  = step_item.get("description", "")
                    cmd   = step_item.get("command", "")
                    if not cmd and stype not in ("python", "mcp_call"):
                        return
                    self._current_step_args = step_item.get("args") or {}
                    result = self._run_with_healing(
                        cmd, stype, task_id, sid,
                        step_item.get("tool",""), label=f"S{sid}", lock=lk, desc=desc
                    )
                    out = result["stdout"][:2500] or result["stderr"][:500]
                    br[sid] = f"[Step {sid} — {desc}]\n$ {result['command']}\n{out}"
                    step_stdout[sid] = result.get("stdout", "").strip()

                for step_item in batch:
                    t = threading.Thread(target=_worker, args=(step_item,), daemon=True)
                    threads.append(t)
                    t.start()
                try:
                    for t in threads:
                        while t.is_alive():
                            t.join(timeout=0.5)
                except KeyboardInterrupt:
                    self._lprint(c("yellow", "\n  ⚡ Ctrl+C — waiting for parallel steps..."))
                    for t in threads:
                        t.join(timeout=5)
                    if not self._ask_continue():
                        self._abort = True
                        break

                results.update(batch_results)
                done = len([v for v in batch_results.values() if v])
                self._lprint(c("green", f"  ✓ Batch done — {done}/{len(batch)} completed"))

        ordered = [results[s.get("id")] for s in plan.get("steps", []) if s.get("id") in results]
        return "\n".join(ordered) if ordered else "No steps were executed."

    def _ask_continue(self) -> bool:
        # In Telegram mode engine has _tg_mode set — auto-continue
        if getattr(self, "_tg_mode", False):
            print(c("dim", "  [Telegram] Auto-continuing..."))
            return True
        print()
        try:
            with self._print_lock:
                ans = input(c("yellow", "  Continue remaining steps? [Y/n]: ")).strip().lower()
            return ans in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    @staticmethod
    def _is_install_cmd(cmd: str) -> bool:
        return bool(re.search(r'(apt-get|apt|snap|pip|pip3)\s+(install)', cmd))

    @staticmethod
    def _extract_pkg(cmd: str) -> str:
        after  = re.sub(r'^.*?install', '', cmd).strip()
        tokens = [t for t in after.split() if not t.startswith('-')]
        return tokens[0] if tokens else cmd.strip().split()[0]

    @staticmethod
    def _strip_sudo(cmd: str) -> str:
        return re.sub(r'^(echo\s+\S+\s*\|\s*)?sudo(\s+-[A-Za-z]+)*\s+', '', cmd).strip()

    @staticmethod
    def _expand_tilde(cmd: str, real_home: str) -> str:
        if '~' not in cmd:
            return cmd
        return re.sub(r'(?<![A-Za-z0-9_])~(?=/|\s|$)', real_home, cmd)

    def _get_real_home(self) -> str:
        sudo_user = os.environ.get("SUDO_USER")
        return os.path.expanduser(f"~{sudo_user}") if sudo_user else os.path.expanduser("~")

    def _log(self, task_id, step_id, tool, cmd, stdout, status):
        self.memory.log_task_step(task_id, step_id, tool, cmd, stdout[:2000], status)

    def _run_with_healing(self, command: str, stype: str,
                          task_id: str, step_id, tool: str,
                          label: str = "", lock: threading.Lock = None,
                          desc: str = "") -> dict:
        history       = self.memory.get_history(4)
        _lp           = (lambda m: _locked_print(lock, m)) if lock else print
        real_home     = self._get_real_home()
        effective_cwd = self._cwd if (self._cwd and os.path.isdir(self._cwd)) else None
        result        = {"success": False, "stdout": "", "stderr": "",
                         "returncode": -1, "elapsed": 0, "cancelled": False,
                         "command": command}
        last_err = ""

        # ── mcp_call — direct MCP tool invocation ─────────────────
        if stype == "mcp_call":
            mcp_tool   = tool
            mcp_args   = getattr(self, "_current_step_args", {}) or {}
            mcp_client = getattr(self, "_mcp_client", None)
            if not mcp_client:
                _lp(c("red", "  [MCP] No active MCP client — cannot execute mcp_call step."))
                self._log(task_id, step_id, mcp_tool, f"mcp:{mcp_tool}", "", "no_client")
                return {"success": False, "stdout": "", "stderr": "No active MCP client",
                        "returncode": -1, "elapsed": 0, "cancelled": False,
                        "command": f"mcp:{mcp_tool}"}

            # ── inner helper: one attempt ──────────────────────────
            def _mcp_attempt(t_name, t_args):
                _lp(c("cyan", f"\n  [MCP] ⟶ {t_name}({json.dumps(t_args)[:120]})"))
                t0 = time.time()
                raw   = mcp_client.call_tool(t_name, t_args, timeout=120)
                txt   = MCPStdioClient.extract_text_result(raw)
                ela   = round(time.time() - t0, 2)
                is_e  = raw.get("isError", False)
                return raw, txt, ela, is_e

            # ── helper: get real schema for a tool ─────────────────
            def _tool_schema(t_name):
                for t in (mcp_client.list_tools() or []):
                    if t.get("name") == t_name:
                        return t.get("inputSchema") or {}
                return {}

            # ── helper: detect pydantic/validation error ───────────
            def _is_validation_err(txt):
                return ("validation error" in txt.lower() or
                        "field required" in txt.lower() or
                        "missing" in txt.lower() and "input_value" in txt.lower())

            # ── helper: fix args via LLM using real schema ─────────
            def _fix_args(t_name, bad_args, err_txt):
                schema = _tool_schema(t_name)
                props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                req    = schema.get("required", []) if isinstance(schema, dict) else []
                # Build param hint: name*(type): description
                hints  = []
                for pn, pi in (props.items() if isinstance(props, dict) else []):
                    star = "*" if pn in req else ""
                    pt   = pi.get("type","any") if isinstance(pi, dict) else "any"
                    pd   = pi.get("description","") if isinstance(pi, dict) else ""
                    hints.append(f"  {pn}{star}({pt}): {pd[:60]}")
                schema_hint = "\n".join(hints) if hints else "(no schema available)"
                prompt = (
                    f"MCP tool call failed with a validation/argument error.\n"
                    f"Tool   : {t_name}\n"
                    f"BadArgs: {json.dumps(bad_args)}\n"
                    f"Error  : {err_txt[:400]}\n\n"
                    f"REAL PARAMETER SCHEMA for {t_name}:\n{schema_hint}\n\n"
                    f"Return ONLY a JSON object with the corrected arguments using the EXACT "
                    f"parameter names shown above. Nothing else — just the JSON object."
                )
                try:
                    agent   = FreeLLM(model=self.model)
                    raw_fix = agent.ask(prompt).strip()
                    fixed   = extract_json(raw_fix)
                    if isinstance(fixed, dict):
                        return fixed
                except Exception:
                    pass
                return None

            # ── attempt 1 ─────────────────────────────────────────
            start = time.time()
            try:
                raw_result, text_out, elapsed, is_err = _mcp_attempt(mcp_tool, mcp_args)
            except Exception as e:
                elapsed = round(time.time() - start, 2)
                err_str = str(e)
                _lp(c("red", f"  [MCP] ✗ {mcp_tool} exception: {err_str[:200]}"))
                self._log(task_id, step_id, mcp_tool, f"mcp:{mcp_tool}", err_str, "exception")
                return {"success": False, "stdout": "", "stderr": err_str,
                        "returncode": -1, "elapsed": elapsed, "cancelled": False,
                        "command": f"mcp:{mcp_tool}"}

            # ── if validation error → fix args and retry once ──────
            if is_err and _is_validation_err(text_out):
                _lp(c("yellow", f"  [MCP] ⚠ arg mismatch — fixing schema for {mcp_tool}..."))
                fixed_args = _fix_args(mcp_tool, mcp_args, text_out)
                if fixed_args and fixed_args != mcp_args:
                    _lp(c("dim",    f"  [MCP] corrected args: {json.dumps(fixed_args)[:120]}"))
                    try:
                        raw_result, text_out, elapsed, is_err = _mcp_attempt(mcp_tool, fixed_args)
                        mcp_args = fixed_args
                    except Exception as e2:
                        elapsed  = round(time.time() - start, 2)
                        text_out = str(e2)
                        is_err   = True
                else:
                    _lp(c("red", "  [MCP] could not determine corrected args"))

            # ── final result ───────────────────────────────────────
            elapsed = round(time.time() - start, 2)
            if is_err:
                _lp(c("red", f"  [MCP] ✗ {mcp_tool} error ({elapsed}s)"))
                _lp(c("red", f"  {text_out[:300]}"))
                self.memory.log_mcp_call(mcp_client.name, mcp_tool,
                                         json.dumps(mcp_args), text_out[:500], "error")
                self._log(task_id, step_id, mcp_tool, f"mcp:{mcp_tool}", text_out, "error")
                return {"success": False, "stdout": text_out, "stderr": "",
                        "returncode": 1, "elapsed": elapsed, "cancelled": False,
                        "command": f"mcp:{mcp_tool}"}
            else:
                _lp(c("green", f"  [MCP] ✓ {mcp_tool} ({elapsed}s)"))
                for ln in text_out.splitlines()[:40]:
                    _lp(c("dim", "  │ ") + ln)
                extra = text_out.count("\n") - 40
                if extra > 0:
                    _lp(c("dim", f"  │ ... ({extra} more lines)"))
                self.memory.log_mcp_call(mcp_client.name, mcp_tool,
                                         json.dumps(mcp_args), text_out[:500], "success")
                self._log(task_id, step_id, mcp_tool, f"mcp:{mcp_tool}", text_out, "success")
                return {"success": True, "stdout": text_out, "stderr": "",
                        "returncode": 0, "elapsed": elapsed, "cancelled": False,
                        "command": f"mcp:{mcp_tool}"}

        if stype != "python" and self._is_install_cmd(command):
            pkg = self._extract_pkg(command)
            _lp(c("cyan", f"\n  [Installer] → {command}"))
            ok, method, _ = self.analyzer.installer.install(pkg)
            self._log(task_id, step_id, tool, command, "", "success" if ok else "failed")
            if ok:
                return {"success": True, "stdout": f"Installed {pkg} via {method}",
                        "stderr": "", "returncode": 0, "elapsed": 0,
                        "cancelled": False, "command": command}
            return {"success": False, "stdout": "", "stderr": f"Install failed: {pkg}",
                    "returncode": 1, "elapsed": 0, "cancelled": False, "command": command}

        if stype != "python" and command.strip():
            # ── Telegram mode: background-launch GUI / non-closing tools ──
            _GUI = {
                "firefox","chromium","chromium-browser","google-chrome",
                "brave-browser","xdg-open","nautilus","thunar","dolphin",
                "vlc","mpv","gimp","inkscape","libreoffice","evince",
                "code","wireshark","burpsuite","zaproxy",
                "gedit","mousepad","kate","discord","slack","telegram",
            }
            _bin0 = command.strip().split()[0].split("/")[-1] if command.strip() else ""
            # Also detect xdg-open buried inside su -c '...' wrapper
            _is_gui_cmd = (
                _bin0 in _GUI
                or any(f" {g}" in command or f"'{g}" in command or f'"{g}' in command
                       for g in _GUI)
            )
            if getattr(self, "_tg_mode", False) and _is_gui_cmd:
                _lp(c("cyan", f"\n  [TG] Launching in background: {command[:80]}"))
                try:
                    _cmd_for_bg = command
                    if _bin0 in _GUI:
                        _cmd_for_bg = CommandExecutor.as_user(command)
                    subprocess.Popen(
                        _cmd_for_bg, shell=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        cwd=effective_cwd or None,
                    )
                    out_msg = f"✅ Launched in background: {command[:100]}"
                    _lp(c("green", f"  {out_msg}"))
                    self._log(task_id, step_id, tool, command, out_msg, "success")
                    return {"success": True, "stdout": out_msg, "stderr": "",
                            "returncode": 0, "elapsed": 0,
                            "cancelled": False, "command": command}
                except Exception as e:
                    _lp(c("yellow", f"  [TG] Background launch failed: {e} — falling through"))

            bin_name = command.strip().split()[0].split("/")[-1]
            if bin_name and not shutil.which(bin_name) and bin_name not in (
                "echo", "cat", "ls", "rm", "mv", "cp", "mkdir", "chmod",
                "chown", "touch", "grep", "awk", "sed", "find", "curl",
                "wget", "ping", "ssh", "scp", "tar", "zip", "unzip",
                "python3", "python", "bash", "sh", "which", "true", "false",
            ):
                _lp(c("yellow", f"\n  [Pre-flight] '{bin_name}' not found — installing before run..."))
                ok, method, _ = self.analyzer.installer.install(bin_name)
                if ok:
                    _lp(c("green", f"  [Pre-flight] ✓ Installed {bin_name} via {method}"))
                else:
                    _lp(c("yellow", f"  [Pre-flight] ✗ Could not install {bin_name} — will try alternative"))
                    alt_cmd = self.analyzer.suggest_alternative(command, desc or command, f"{bin_name}: command not found")
                    if alt_cmd:
                        alt_bin = alt_cmd.strip().split()[0].split("/")[-1]
                        if alt_bin and not shutil.which(alt_bin):
                            _lp(c("dim", f"  [Pre-flight] Installing alt tool: {alt_bin}"))
                            self.analyzer.installer.install(alt_bin)
                        alt_cmd = self._expand_tilde(alt_cmd, real_home)
                        _lp(c("cyan", f"  [Pre-flight] Alternative: {alt_cmd[:80]}"))
                        result = self.cmd_exec.run(alt_cmd, label=label, lock=lock, cwd=effective_cwd)
                        self._log(task_id, step_id, tool, alt_cmd,
                                  result["stdout"], "success" if result["success"] else "error")
                        if result["success"]:
                            return result
                        last_err = result["stderr"] or result["stdout"]
                        command = alt_cmd

        for attempt in range(1, MAX_RETRIES + 1):
            if stype == "python":
                retry_task = desc or command[:120]
                if attempt > 1:
                    retry_task += f"\nPrevious error: {last_err[:200]}"
                result = self.py_exec.generate_and_run(
                    task=retry_task,
                    user_input=getattr(self, "_user_input", command) or command,
                    cwd=effective_cwd or "/root",
                    model=self.model,
                )
            else:
                _cmd = self._expand_tilde(command, real_home)
                _GUI = {
                    "firefox","chromium","chromium-browser","google-chrome",
                    "brave-browser","xdg-open","nautilus","thunar","dolphin",
                    "vlc","mpv","gimp","inkscape","libreoffice","evince",
                    "code","wireshark","burpsuite","zaproxy",
                    "gedit","mousepad","kate","discord","slack","telegram",
                }
                _bin = _cmd.strip().split()[0].split("/")[-1] if _cmd.strip() else ""
                if _bin in _GUI:
                    _cmd = CommandExecutor.as_user(_cmd)
                result = self.cmd_exec.run(_cmd, label=label, lock=lock, cwd=effective_cwd)

            if result.get("cancelled"):
                return result
            self._log(task_id, step_id, tool, command,
                      result["stdout"], "success" if result["success"] else "error")
            if result["success"]:
                return result
            last_err = result["stderr"] or result["stdout"]
            if stype == "python":
                if attempt < MAX_RETRIES:
                    _lp(c("yellow", f"  ⚠ Python attempt {attempt}/{MAX_RETRIES} failed — regenerating..."))
                continue
            if "command not found" in last_err.lower() or "exit:127" in last_err.lower():
                _lp(c("red", "  ✗ Binary still not found after install attempt — escalating."))
                break
            perm_err = any(kw in last_err.lower() for kw in [
                "permission denied", "operation not permitted",
                "authentication failure", "not in the sudoers",
            ])
            if perm_err:
                _lp(c("yellow", "  ⚠ Permission error — escalating..."))
                break
            if attempt < MAX_RETRIES:
                _lp(c("yellow", f"  ⚠ Attempt {attempt}/{MAX_RETRIES} — rebuilding command..."))
                fixed = self.analyzer.analyze_and_fix(command, last_err, history)
                if fixed and fixed.strip() != command.strip():
                    _lp(c("magenta", f"  ↺ Rebuilt: {fixed[:80]}"))
                    command = fixed
                else:
                    _lp(c("red", "  ✗ No fix found — escalating."))
                    break

        _out_lower = result.get("stdout", "").lower()
        _err_lower = last_err.lower()
        _soft_pats = [
            "no such file or directory", "cannot access", "not found",
            "no matches found", "0 directories, 0 files", "total 0",
        ]
        _is_soft = any(p in _err_lower or p in _out_lower for p in _soft_pats)
        if _is_soft:
            _lp(c("yellow", "  ⚠ Path not found or empty — reporting as-is."))
            result["success"]    = True
            result["stdout"]     = (last_err.strip() or result.get("stdout","").strip()
                                    or "Directory not found or empty.")
            result["returncode"] = 0
            return result

        if stype == "python":
            _lp(c("red", f"  ✗ Step {step_id} — Python failed after {MAX_RETRIES} attempts."))
            return result

        _lp(c("red", "  ✗ Phase 1 exhausted — escalating..."))
        clean    = self._strip_sudo(command)
        bin_name = clean.split()[0].split("/")[-1] if clean.split() else ""
        if bin_name and not shutil.which(bin_name):
            _lp(c("yellow", f"  [Phase 2] '{bin_name}' not in PATH — installing..."))
            ok, method, _ = self.analyzer.installer.install(bin_name)
            if ok:
                _lp(c("green", f"  [Phase 2] ✓ Installed via {method} — retrying..."))
                result = self.cmd_exec.run(
                    self._expand_tilde(clean, real_home),
                    label=label, lock=lock, cwd=effective_cwd
                )
                self._log(task_id, step_id, tool, clean,
                          result["stdout"], "success" if result["success"] else "error")
                if result["success"]:
                    return result
                last_err = result["stderr"] or result["stdout"]

        alt_cmd = self.analyzer.suggest_alternative(command, desc or command, last_err)
        if alt_cmd:
            alt_bin = alt_cmd.strip().split()[0].split("/")[-1]
            if alt_bin and not shutil.which(alt_bin):
                _lp(c("dim", f"  [Phase 3] Installing alt tool: {alt_bin}"))
                self.analyzer.installer.install(alt_bin)
            alt_cmd = self._expand_tilde(alt_cmd, real_home)
            _lp(c("cyan", f"  [Phase 3] Alternative: {alt_cmd[:80]}"))
            result = self.cmd_exec.run(alt_cmd, label=label, lock=lock, cwd=effective_cwd)
            self._log(task_id, step_id, tool, alt_cmd,
                      result["stdout"], "success" if result["success"] else "error")
            if result["success"]:
                _lp(c("green", "  [Phase 3] ✓ Alternative succeeded"))
                return result
            last_err = result["stderr"] or result["stdout"]

        _lp(c("magenta", "  [Phase 4] Python fallback..."))
        fb = PythonFallback(self.model, self.py_exec, self.cmd_exec)
        result = fb.generate_and_run(command, desc or command, last_err, cwd=effective_cwd)
        self._log(task_id, step_id, tool, f"[py-fallback]{command}",
                  result["stdout"], "success" if result["success"] else "fallback_failed")
        if result["success"]:
            return result

        _lp(c("red", f"  ✗ Step {step_id} exhausted all recovery phases."))
        return result


class DynamicExecutionEngine:
    def __init__(self, memory: MemoryDB, model: str = DEFAULT_MODEL):
        self.memory  = memory
        self.model   = model
        self.engine  = ExecutionEngine(memory, model)
        self.planner = PlannerEngine()

    def run(self, original_task: str, initial_plan: dict,
            task_id: str, profile: dict) -> str:
        self.engine._user_input = original_task
        completed   = []
        all_results = []
        steps = initial_plan.get("steps", [])

        while True:
            if not steps:
                break
            independent = [s for s in steps if not s.get("depends_on")]
            dependent   = [s for s in steps if s.get("depends_on")]
            if not independent:
                independent = steps
                dependent   = []

            print(c("cyan", f"\n  ⚡ Running {len(independent)} independent step(s)..."))
            mini_plan = {**initial_plan, "steps": independent}
            raw       = self.engine.execute_plan(mini_plan, task_id)
            all_results.append(raw)

            for step in independent:
                sid  = step.get("id")
                desc = step.get("description", "")
                with sqlite3.connect(self.memory.db_path) as conn:
                    row = conn.execute(
                        "SELECT output FROM task_memory WHERE task_id=? AND step=? ORDER BY id DESC LIMIT 1",
                        (task_id, sid)
                    ).fetchone()
                output = row[0] if row else ""
                completed.append({"id": sid, "desc": desc, "output": output})

            if not dependent and not completed:
                break
            if not dependent:
                print(c("dim", "\n  [→] Checking if more steps needed..."))
                next_plan = self.planner.plan_next(
                    original_task, completed, profile, self.model
                )
                if not next_plan or not next_plan.get("steps"):
                    print(c("green", "  ✓ Task complete."))
                    break
                steps = next_plan["steps"]
            else:
                print(c("dim", "\n  [→] Generating dependent steps with real output..."))
                next_plan = self.planner.plan_next(
                    original_task, completed, profile, self.model
                )
                if not next_plan or not next_plan.get("steps"):
                    break
                steps = next_plan["steps"]

        return "\n\n".join(all_results) if all_results else "No steps executed."

# ══════════════════════════════════════════════════════════════
# SECTION 16 — SUMMARIZER
# ══════════════════════════════════════════════════════════════

class Summarizer:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    def summarize(self, raw_results: str, original_request: str, history: list) -> str:
        system_ctx = textwrap.dedent(f"""
You are Hackers AI. Write a SHORT, accurate summary of what just happened.

Rules:
- Report ONLY what actually happened for THIS task
- No generic templates, no filler bullets
- XSS/web test → state which payloads reflected, which were blocked, any confirmed vulns
- Scan → show actual findings (open ports, services, vulnerabilities found)
- Install → state what was installed and result
- Simple command → result in 1-2 sentences
- PLAIN TEXT ONLY — no markdown, no ``` fences, no ** bold, no # headers

Task: {original_request}
        """).strip()
        agent    = FreeLLM(model=self.model)
        response = agent.ask(system_ctx + f"\n\nOutput:\n{raw_results[:4000]}")
        # Strip markdown and shell prompt artifacts
        response = re.sub(r"```[a-zA-Z]*\n?", "", response)
        response = re.sub(r"```", "", response)
        response = re.sub(r"\*\*([^*]+)\*\*", r"\1", response)
        response = re.sub(r"^#{1,6}\s+", "", response, flags=re.MULTILINE)
        response = re.sub(r"^\s*\([^)]*㉿[^)]*\)-\[.*?\]\$\s*", "", response, flags=re.MULTILINE)
        response = re.sub(r"^\s*root@\S+:[^\$#]*[#\$]\s*", "", response, flags=re.MULTILINE)
        return response.strip()

# ══════════════════════════════════════════════════════════════
# SECTION 17 — INTENT CLASSIFIER
# ══════════════════════════════════════════════════════════════

class IntentClassifier:
    SYSTEM_CTX = (
        "You are an intent classifier for a Linux AI agent. "
        "Decide if the user input requires EXECUTING something (task) "
        "or just ANSWERING a question with no system action (informational).\n\n"
        "TASK = anything that needs a shell command, opens an app, creates/modifies "
        "files, runs a scan, plays media, searches the web, installs software, "
        "checks if a port is open, tests connectivity, or does ANYTHING on the system "
        "— even if phrased as a question like 'is port 80 open?' or 'can you ping X?'.\n\n"
        "INFORMATIONAL = pure conceptual questions with no system action needed: "
        "'what is nmap', 'how does XSS work', 'what is a firewall'.\n\n"
        "CRITICAL: Questions about the state of the system or network (open ports, "
        "running processes, disk usage, connectivity) are ALWAYS task — they require "
        "running a command to check. When in doubt → task.\n\n"
        "Examples:\n"
        "  'scan 192.168.0.1'             → task\n"
        "  'is port 80 open on 10.0.0.1' → task\n"
        "  'check if ssh is running'      → task\n"
        "  'open firefox'                 → task\n"
        "  'create a folder test'         → task\n"
        "  'play music on youtube'        → task\n"
        "  'what is nmap'                 → informational\n"
        "  'how does xss work'            → informational\n"
        "  'hello'                        → informational\n\n"
        "Reply with ONLY one word: task  or  informational"
    )

    # Patterns that are ALWAYS a task — checked before calling the LLM
    _TASK_PATTERNS = re.compile(
        r'\b(scan|nmap|ping|curl|wget|ssh|ftp|run|exec|open|start|stop|restart|'
        r'install|uninstall|remove|create|delete|mkdir|rm |mv |cp |cat |ls |find|'
        r'check|test|is\s+port|port\s+\d+|enumerate|brute|fuzz|recon|exploit|'
        r'crack|hash|connect|listen|upload|download|deploy|kill|ps |top |df |du |'
        r'grep|awk|sed|chmod|chown|ln |tar |zip|unzip|netstat|ss |ip |ifconfig|'
        r'traceroute|dig |host |nslookup|hydra|gobuster|ffuf|sqlmap|nikto|nuclei|'
        r'show\s+(me\s+)?(files?|port|process|service|disk|memory|cpu|network|ip))\b',
        re.IGNORECASE
    )

    @classmethod
    def classify(cls, text: str, model: str = DEFAULT_MODEL) -> str:
        # Fast path: regex catches obvious action queries without an LLM call
        if cls._TASK_PATTERNS.search(text):
            return "task"
        try:
            agent  = FreeLLM(model=model)
            result = agent.ask(f"{cls.SYSTEM_CTX}\n\nUser input: {text}").strip().lower()
            # "task" takes priority — only return informational if explicitly stated
            # and "task" is NOT also present
            if "informational" in result and "task" not in result:
                return "informational"
        except Exception:
            pass
        return "task"

# ══════════════════════════════════════════════════════════════
# SECTION 18 — RECON PIPELINE
# ══════════════════════════════════════════════════════════════

class ReconPipeline:
    REQUIRED_TOOLS = [
        ("subfinder",      "subdomain enumeration"),
        ("sublist3r",      "subdomain enumeration"),
        ("httpx-toolkit",  "live host filter"),
        ("wafw00f",        "WAF detection"),
        ("nmap",           "port scanner"),
        ("gobuster",       "directory brute-force"),
        ("nikto",          "web vulnerability scanner"),
        ("whatweb",        "web tech fingerprint"),
        ("nuclei",         "template-based vuln scanner"),
    ]

    def __init__(self, model: str, memory: "MemoryDB", outbase: str = "/tmp"):
        self.model       = model
        self.memory      = memory
        self.outbase     = outbase
        self.results     = {}
        self._abort      = False
        self._skip_stage = False
        self._wl_finder  = WordlistFinder()

    def _run(self, cmd: str, desc: str, timeout: int = 300) -> str:
        print(c("cyan", f"\n  ┌─ [{desc}]"))
        print(c("dim",  f"  │  $ {cmd}"))
        lines = []
        proc  = None
        try:
            proc = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, preexec_fn=os.setsid
            )
            for line in iter(proc.stdout.readline, ""):
                if self._skip_stage or self._abort:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
                    break
                line = line.rstrip()
                if line:
                    print(c("dim", "  │  ") + line)
                    lines.append(line)
            proc.stdout.close()
            proc.wait(timeout=5)
            icon = c("green","✓") if proc.returncode == 0 else c("yellow","~")
            print(c("dim", f"  └─ {icon} done ({len(lines)} lines)"))
        except subprocess.TimeoutExpired:
            if proc:
                try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception: pass
            print(c("yellow", f"  └─ ⏱ Timeout after {timeout}s"))
        except Exception as e:
            print(c("red", f"  └─ ✗ {e}"))
        return "\n".join(lines)

    def _has(self, tool: str) -> bool:
        return bool(shutil.which(tool))

    def _install_tools(self):
        missing = [t for t, _ in self.REQUIRED_TOOLS if not self._has(t)]
        if not missing:
            print(c("green", "  ✓ All recon tools ready"))
            return
        print(c("yellow", f"\n  Installing {len(missing)} tools: {', '.join(missing)}"))
        os.system("apt-get update -qq 2>/dev/null")
        for tool in missing:
            ret = os.system(f"apt-get install -y {shlex.quote(tool)} 2>/dev/null")
            if not (ret == 0 or self._has(tool)):
                print(c("yellow", f"  ~ {tool} unavailable — stage will be skipped"))

    def run(self, domain: str) -> str:
        domain  = re.sub(r"^https?://", "", domain.strip()).split("/")[0].strip()
        safe_d  = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir  = os.path.join(self.outbase, f"recon_{safe_d}_{ts}")
        os.makedirs(outdir, exist_ok=True)
        wordlists = self._wl_finder.scan()

        print(c("green", "\n  ╔══ RECON PIPELINE ══════════════════════════════════════════"))
        print(c("green", f"  ║  Target : {c('white', domain)}"))
        print(c("green", f"  ║  Output : {outdir}"))
        print(c("green",  "  ╚" + "═"*62))

        report_parts = [
            f"# Recon Report: {domain}",
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
            f"*Output dir: {outdir}*", ""
        ]

        print(c("yellow", "\n  ━━ Stage 0 — Tool Check ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        self._install_tools()

        print(c("yellow", "\n  ━━ Stage 1 — Subdomain Enumeration ━━━━━━━━━━━━━━━━━━"))
        subs_file = os.path.join(outdir, "subdomains.txt")
        all_subs  = []
        if self._has("subfinder"):
            self._run(
                f"subfinder -d {shlex.quote(domain)} -silent -o {shlex.quote(os.path.join(outdir,'subfinder.txt'))}",
                "subfinder", 120
            )
        if self._has("sublist3r"):
            self._run(
                f"sublist3r -d {shlex.quote(domain)} -o {shlex.quote(os.path.join(outdir,'sublist3r.txt'))} 2>/dev/null",
                "sublist3r", 180
            )
        seen_subs = set()
        for src in [os.path.join(outdir, "subfinder.txt"), os.path.join(outdir, "sublist3r.txt")]:
            if os.path.exists(src):
                with open(src) as f:
                    for line in f:
                        line = line.strip()
                        if line and line not in seen_subs:
                            seen_subs.add(line)
                            all_subs.append(line)
        with open(subs_file, "w") as f:
            f.write("\n".join(all_subs) + f"\n{domain}\n")
        print(c("green", f"\n  ✓ {len(all_subs)} unique subdomains"))
        report_parts += [f"## Subdomains ({len(all_subs)})", "```",
                         "\n".join(all_subs[:100]) or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 2 — Live Host Filtering ━━━━━━━━━━━━━━━━━━━━"))
        live_file = os.path.join(outdir, "live.txt")
        live_raw  = ""
        httpx_bin = "httpx-toolkit" if self._has("httpx-toolkit") else ("httpx" if self._has("httpx") else "")
        if httpx_bin:
            live_raw = self._run(
                f"{httpx_bin} -l {shlex.quote(subs_file)} -silent "
                f"-o {shlex.quote(live_file)} -status-code -title 2>/dev/null",
                httpx_bin, 180
            )
        report_parts += ["## Live Hosts", "```", live_raw[:2000] or "none", "```", ""]

        scan_targets = []
        if os.path.exists(live_file):
            with open(live_file) as lf:
                for _line in lf:
                    _url = _line.strip().split()[0].strip()
                    if _url.startswith("http") and _url not in scan_targets:
                        scan_targets.append(_url)
        if not scan_targets:
            scan_targets = [f"http://{domain}"]

        print(c("yellow", "\n  ━━ Stage 3 — WAF Detection ━━━━━━━━━━━━━━━━━━━━━━━━━"))
        waf_raw = ""
        if self._has("wafw00f"):
            for tgt in scan_targets[:3]:
                waf_raw += self._run(f"wafw00f {shlex.quote(tgt)} 2>/dev/null", "wafw00f", 60)
        report_parts += ["## WAF Detection", "```", waf_raw[:1500] or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 4 — Port Scan ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        port_raw = ""
        PORTS = "21,22,23,25,53,80,110,143,443,445,1433,3306,3389,5432,5900,6379,8080,8443,9200,27017"
        if self._has("nmap"):
            for tgt in scan_targets[:3]:
                host = re.sub(r"^https?://", "", tgt).split("/")[0]
                port_raw += self._run(
                    f"nmap -T4 -sV --open -Pn -p {PORTS} {shlex.quote(host)} 2>/dev/null",
                    f"nmap {host}", 240
                )
        report_parts += ["## Port Scan", "```", port_raw[:3000] or "no open ports", "```", ""]

        print(c("yellow", "\n  ━━ Stage 5 — Directory Brute-Force ━━━━━━━━━━━━━━━━━"))
        dir_raw  = ""
        wordlist = wordlists.get("dirs", "")
        if wordlist and self._has("gobuster"):
            for tgt in scan_targets[:2]:
                dir_raw += self._run(
                    f"gobuster dir -u {shlex.quote(tgt)} -w {shlex.quote(wordlist)} "
                    f"-t 50 -q --no-error 2>/dev/null",
                    "gobuster", 300
                )
        elif not wordlist:
            print(c("yellow", "  [!] No directory wordlist found — skipping. Install seclists."))
        report_parts += ["## Directories", "```", dir_raw[:3000] or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 6 — Web Tech Fingerprint ━━━━━━━━━━━━━━━━━━"))
        tech_raw = ""
        if self._has("whatweb"):
            for tgt in scan_targets[:3]:
                tech_raw += self._run(
                    f"whatweb -a 3 --no-errors {shlex.quote(tgt)} 2>/dev/null", "whatweb", 60
                )
        report_parts += ["## Web Technologies", "```", tech_raw[:2000] or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 7 — Nikto Scan ━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        nikto_raw = ""
        if self._has("nikto"):
            for tgt in scan_targets[:2]:
                nikto_raw += self._run(
                    f"nikto -h {shlex.quote(tgt)} -nointeractive 2>/dev/null", "nikto", 300
                )
        report_parts += ["## Nikto Findings", "```", nikto_raw[:4000] or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 8 — Nuclei ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        nuclei_raw = ""
        if self._has("nuclei"):
            os.system("nuclei -update-templates -silent 2>/dev/null &")
            for tgt in scan_targets[:2]:
                nuclei_raw += self._run(
                    f"nuclei -u {shlex.quote(tgt)} -severity low,medium,high,critical "
                    f"-silent 2>/dev/null",
                    "nuclei", 300
                )
        report_parts += ["## Nuclei Findings", "```", nuclei_raw[:5000] or "none", "```", ""]

        print(c("yellow", "\n  ━━ Stage 9 — AI Assessment ━━━━━━━━━━━━━━━━━━━━━━━━━"))
        try:
            prompt = (
                f"You are a penetration tester. Analyze recon results for {domain}.\n\n"
                f"WAF: {waf_raw[:300] or 'unknown'}\n"
                f"TECH: {tech_raw[:300] or 'unknown'}\n"
                f"PORTS: {port_raw[:800] or 'none'}\n"
                f"NIKTO: {nikto_raw[:1500] or 'none'}\n"
                f"NUCLEI: {nuclei_raw[:1500] or 'none'}\n\n"
                "Provide:\n1. CRITICAL FINDINGS\n2. HIGH RISK\n"
                "3. RECOMMENDED NEXT STEPS\n4. RISK SCORE (Critical/High/Medium/Low)\n"
                "Focus on real findings only. Be concise."
            )
            agent      = FreeLLM(model=self.model)
            ai_summary = agent.ask(prompt).strip()
            print(c("red", "  ╔══ AI ASSESSMENT " + "═"*44))
            for line in ai_summary.splitlines():
                sc = "red" if "critical" in line.lower() else \
                     "yellow" if "high" in line.lower() else "white"
                print(c("red", "  ║ ") + c(sc, line))
            print(c("red", f"  ╚{'═'*62}"))
            report_parts += ["## AI Assessment", "", ai_summary, ""]
        except Exception as e:
            print(c("dim", f"  [!] AI summary error: {e}"))

        report_md   = "\n".join(report_parts)
        report_path = os.path.join(outdir, "report.md")
        with open(report_path, "w") as f:
            f.write(report_md)

        print(c("green", f"\n  ✓ Report: {report_path}"))
        return report_md

# ══════════════════════════════════════════════════════════════
# SECTION 19 — CLI INTERFACE
# ══════════════════════════════════════════════════════════════

class CLI:
    GUI_COMMANDS = {
        "firefox","chromium","chromium-browser","google-chrome","brave-browser",
        "xdg-open","nautilus","thunar","dolphin","nemo","pcmanfm",
        "mousepad","gedit","kate","pluma","vlc","mpv",
        "gimp","inkscape","libreoffice","evince","okular","zathura",
        "code","vscode","atom","discord","slack","telegram","signal",
        "xterm","xfce4-terminal","gnome-terminal","konsole","tilix",
        "wireshark","burpsuite","zaproxy",
    }

    SLASH_COMMANDS = {
        "/help":    "Show this help",
        "/clear":   "Clear conversation history",
        "/history": "Show last 10 messages",
        "/profile": "Show live system profile",
        "/tools":   "List detected pentest tools",
        "/sysinfo": "Run live system info commands",
        "/switch":  "Switch model: /switch <model>",
        "/target":  "Set sticky target: /target <domain|IP> or /target clear",
        "/auth":    "Authorize target: /auth add <target> | /auth list | /auth remove <target>",
        "/shell":   "Drop to interactive bash shell",
        "/recon":   "Full recon pipeline: /recon <domain>",
        "/note":    "Save target note: /note <target> <note>",
        "/notes":   "Show notes: /notes [target]",
        "/delnotes":"Delete notes: /delnotes [target]",
        "/save":    "Save session report: /save [filename]",
        "/dryrun":  "Toggle dry-run mode (preview commands only)",
        "/config":  "Open MCP config file in editor (~/.hackers_ai_mcp.json)",
        "/telegram":"Remote control via Telegram: /telegram --api-token T --user-id U",
        "/mcp":     "MCP: /mcp list|use <name>|exit|reload|tools [name]|call <tool> [args]|ai <task>",
        "/exit":    "Exit Hackers AI",
    }

    def __init__(self):
        self.model         = DEFAULT_MODEL
        self.memory        = MemoryDB()
        self.profiler      = SystemProfiler()
        self.scope_guard   = ScopeGuard(self.memory)
        self.dry_run       = False
        self.run_as_user   = False
        self.sticky_target = ""
        # Active MCP client instance (MCPStdioClient or None)
        self._mcp_client: Optional[MCPStdioClient] = None
        # Telegram notifier / remote bridge
        self._tg = TelegramBot()
        # When True: auto-confirm plans, skip interactive prompts (Telegram mode)
        self._tg_mode = False

        _sudo_user = os.environ.get("SUDO_USER")
        self.cwd = os.path.expanduser(f"~{_sudo_user}") if _sudo_user else os.getcwd()

        self._check_proxy()

        print(c("dim", "  [*] Profiling system..."), end="", flush=True)
        self.profile = self.profiler.profile()
        print(c("green", " done"))

        # Auto-restore last active MCP server
        self._restore_mcp()

        # Auto-start Telegram bot if previously configured
        self._tg_autostart()

        last = self.memory.get_last_session_summary()
        if last:
            print(c("dim", f"\n  Last session: {last}"))
            try:
                ans = input(c("cyan", "  Restore previous session history? [y/N]: ")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans not in ("y", "yes"):
                self.memory.clear_history()
                print(c("dim", "  Starting fresh session.\n"))
            else:
                print(c("green", "  ✓ Session restored.\n"))
        else:
            self.memory.clear_history()

    def _restore_mcp(self):
        """Re-attach the last active MCP server on startup."""
        active_name = self.memory.get_mcp_active_name()
        if not active_name:
            return
        cfg = _mcp_config_load()
        servers = cfg.get("mcpServers", {})
        if active_name not in servers:
            # Config no longer has this server — clear stale state
            self.memory.clear_mcp_active()
            return
        srv_cfg = servers[active_name]
        self._mcp_client = _mcp_client_from_config(active_name, srv_cfg)
        print(c("dim", f"  [MCP] Restoring '{active_name}'..."), end="", flush=True)
        try:
            self._mcp_client.initialize()
            n = len(self._mcp_client.list_tools())
            print(c("green", f" ✓ {n} tools"))
        except Exception as e:
            print(c("yellow", f" could not connect ({e})"))
            self._mcp_client = None

    def _tg_autostart(self):
        """Start Telegram bot automatically if it was previously configured and enabled."""
        cfg = _tg_config_load()
        if cfg.get("token") and cfg.get("user_id") and cfg.get("enabled", False):
            self._tg.start(self)
            print(c("green", f"  [✓] Telegram bot started  (user_id={cfg['user_id']})"))

    def _check_proxy(self):
        try:
            req = urllib.request.Request(
                f"{PROXY_BASE_URL}/health",
                headers={"x-api-key": "local-proxy-key"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            print(c("green", f"  [✓] Proxy reachable: {PROXY_BASE_URL}"))
        except Exception:
            print(c("yellow", f"\n  [!] WARNING: Proxy not reachable at {PROXY_BASE_URL}"))
            print(c("yellow",  "  [!] Start it first:  python server.py [--headless]"))
            print(c("yellow",  "  [!] Or set env var:  export HACKERS_AI_PROXY=http://host:port\n"))

    def _get_prompt(self) -> str:
        try:
            _sudo_user = os.environ.get("SUDO_USER") or "root"
            _hostname  = self.profile.get("hostname", "kali") or "kali"
            home = (os.path.expanduser(f"~{_sudo_user}") if os.environ.get("SUDO_USER")
                    else os.path.expanduser("~"))
            display = self.cwd
            if display == home:
                display = "~"
            elif display.startswith(home + "/"):
                display = "~" + display[len(home):]
        except Exception:
            display    = self.cwd
            _sudo_user = "root"
            _hostname  = "kali"

        target_str = ""
        if self.sticky_target:
            target_str = c("dim", f"[{self.sticky_target}]") + "\n"

        mcp_str = ""
        if self._mcp_client:
            mcp_str = c("dim", f"[mcp:{self._mcp_client.name}] ")

        user_col = "red" if not self.run_as_user else "green"
        return (
            target_str +
            mcp_str +
            c("dim", "(") + c(user_col, _sudo_user) +
            c("dim", "㉿") + c("cyan", _hostname) +
            c("dim", ")-[") + c("yellow", display) +
            c("dim", "]") + c("white", "$ ")
        )

    def _print_banner(self):
        print(c("green", BANNER))
        root_str = c("red", "● ROOT") if self.profile["root"] else c("yellow", "● USER")
        n_tools  = len(self.profile["available_tools"])
        print(c("dim", f"  Model    : {self.model}  |  Agent v{VERSION}"))
        print(c("dim", f"  Backend  : {PROXY_BASE_URL}"))
        print(c("dim", f"  OS       : {self.profile.get('distro','Linux')}  |  "
                        f"Kernel {self.profile.get('kernel','')}  |  {self.profile.get('arch','')}"))
        print(c("dim", f"  Host     : {self.profile.get('hostname','')}  |  "
                        f"IP {self.profile.get('ip','')}  |  {root_str}"))
        print(c("dim", f"  Hardware : CPU {self.profile.get('cpu','')}  |  RAM {self.profile.get('ram','')}"))
        print(c("dim", f"  CWD      : {self.cwd}"))
        print(c("dim", f"  MCP Cfg  : {MCP_CONFIG_PATH}  (edit with /config)"))
        _tg_status = self._tg.status_str
        print(c("dim", f"  Telegram : {_tg_status}  (/telegram to configure remote control)"))
        print(c("dim", f"  Tools    : {n_tools} tools detected  |  DB: {DB_PATH}"))
        if self.dry_run:
            print(c("yellow", "  ⚡ DRY-RUN mode active"))
        print(c("dim", "  Type /help for commands\n"))

    def _handle_slash(self, cmd: str) -> bool:
        parts = cmd.strip().split(None, 1)
        slug  = parts[0].lower()
        arg   = parts[1].strip() if len(parts) > 1 else ""

        if slug == "/help":
            print()
            print(c("yellow", "  ╔══ HACKERS AI COMMANDS " + "═"*39))
            cats = [
                ("General",  ["/help","/clear","/history","/profile","/tools","/sysinfo","/switch","/exit"]),
                ("Session",  ["/target","/auth","/shell","/save","/dryrun"]),
                ("Recon",    ["/recon","/note","/notes","/delnotes"]),
                ("MCP",      ["/config","/mcp"]),
                ("Notify",   ["/telegram"]),
            ]
            for cat, keys in cats:
                print(c("yellow", f"  ║  {c('white', cat)}"))
                for k in keys:
                    v = self.SLASH_COMMANDS.get(k, "")
                    print(f"  {c('yellow','║')}    {c('cyan', k):<28} {v}")
            print(c("yellow", "  ╚" + "═"*61))
            print()
            return True

        if slug == "/clear":
            self.memory.clear_history()
            print(c("green", "  ✓ History cleared."))
            return True

        if slug == "/history":
            history = self.memory.get_history(MAX_HISTORY)
            if not history:
                print(c("dim", "  No history yet."))
            else:
                print()
                for i, h in enumerate(history, 1):
                    role_str = c("cyan","YOU") if h["role"]=="user" else c("green"," AI")
                    snippet  = h["content"][:120].replace("\n"," ")
                    print(f"  {i:02d}. [{role_str}] {snippet}")
                print()
            return True

        if slug == "/profile":
            print()
            for k, v in self.profile.items():
                if k in {"available_tools","uname"}:
                    continue
                print(f"  {c('cyan', k+':'): <20} {v}")
            if self.sticky_target:
                print(f"  {c('cyan','sticky_target:'): <20} {self.sticky_target}")
            if self._mcp_client:
                print(f"  {c('cyan','active_mcp:'): <20} {self._mcp_client.name}")
            print()
            return True

        if slug == "/sysinfo":
            ex = CommandExecutor()
            sections = [
                ("OS",      "lsb_release -a 2>/dev/null || cat /etc/os-release"),
                ("Kernel",  "uname -r && uname -m"),
                ("CPU",     "lscpu | grep -E 'Model name|Socket|Core|Thread'"),
                ("Memory",  "free -h"),
                ("Disk",    "lsblk && df -h"),
                ("Network", "ip addr show | grep -E 'inet |link/ether'"),
                ("Uptime",  "uptime"),
            ]
            for label, c_cmd in sections:
                print(c("cyan", f"\n  ── {label} ──"))
                ex.run(c_cmd, timeout=10, cwd=self.cwd)
            return True

        if slug == "/tools":
            tools = self.profile["available_tools"]
            if not tools:
                print(c("yellow", "  No pentest tools detected in PATH."))
            else:
                print(c("green", f"\n  {len(tools)} tools detected:"))
                cols = 5
                for i in range(0, len(tools), cols):
                    row = tools[i:i+cols]
                    print("  " + "  ".join(f"{t:<18}" for t in row))
            print()
            return True

        if slug == "/switch":
            if arg:
                self.model = arg
                print(c("green", f"  ✓ Model: {self.model}"))
            else:
                print(c("red", "  Usage: /switch <model_name>"))
            return True

        if slug == "/target":
            if not arg:
                if self.sticky_target:
                    print(c("cyan", f"  Current target: {c('white', self.sticky_target)}"))
                else:
                    print(c("dim", "  No sticky target set. Usage: /target <domain|IP>"))
            elif arg.lower() == "clear":
                self.sticky_target = ""
                print(c("green", "  ✓ Sticky target cleared."))
            else:
                self.sticky_target = arg
                print(c("green", f"  ✓ Sticky target: {c('white', self.sticky_target)}"))
            return True

        if slug == "/auth":
            sub_parts = arg.split(None, 1)
            sub_cmd   = sub_parts[0].lower() if sub_parts else ""
            sub_arg   = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if sub_cmd == "add":
                if not sub_arg:
                    print(c("red", "  Usage: /auth add <target>"))
                else:
                    self.memory.add_authorized_target(sub_arg)
                    print(c("green", f"  ✓ Authorized: {sub_arg}"))
                    print(c("dim", "  WARNING: Only authorize systems you own or have written permission to test."))
            elif sub_cmd == "list":
                targets = self.memory.get_authorized_targets()
                if not targets:
                    print(c("dim", "  No authorized targets. Use /auth add <target>."))
                else:
                    print()
                    for t in targets:
                        print(f"  {c('green', '●')} {t['target']:<40} {c('dim', t['added'][:16])}")
                    print()
            elif sub_cmd == "remove":
                if not sub_arg:
                    print(c("red", "  Usage: /auth remove <target>"))
                else:
                    self.memory.remove_authorized_target(sub_arg)
                    print(c("green", f"  ✓ Removed: {sub_arg}"))
            else:
                print(c("dim", "  Usage: /auth add <target> | /auth list | /auth remove <target>"))
            return True

        if slug == "/shell":
            print(c("cyan", f"\n  Dropping to bash in {self.cwd}"))
            print(c("dim", "  Type 'exit' to return to Hackers AI\n"))
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user and self.run_as_user:
                env = os.environ.copy()
                env["HOME"] = os.path.expanduser(f"~{sudo_user}")
                subprocess.run(["su", "-s", "/bin/bash", sudo_user], cwd=self.cwd, env=env)
            else:
                subprocess.run(["/bin/bash"], cwd=self.cwd)
            print(c("cyan", "\n  Returned to Hackers AI\n"))
            return True

        if slug in ("/recon", "recon"):
            target = arg or self.sticky_target
            if not target:
                print(c("red", "  Usage: /recon <domain>"))
            else:
                allowed, reason, host = self.scope_guard.check(f"recon {target}", self.sticky_target)
                if allowed is None:
                    if not self._confirm_scope(host):
                        return True
                pipeline = ReconPipeline(self.model, self.memory, outbase=self.cwd)
                pipeline.run(target)
            return True

        if slug == "/note":
            parts2 = arg.split(None, 1)
            if len(parts2) < 2:
                print(c("red", "  Usage: /note <target> <note text>"))
            else:
                self.memory.add_note(parts2[0], parts2[1])
                print(c("green", f"  ✓ Note saved: [{parts2[0]}] {parts2[1]}"))
            return True

        if slug == "/notes":
            notes = self.memory.get_notes(arg or None)
            if not notes:
                print(c("dim", "  No notes found."))
            else:
                print()
                for n in notes:
                    print(f"  {c('cyan', n['target']):<30} {n['note']}  {c('dim', n['timestamp'][:16])}")
                print()
            return True

        if slug == "/delnotes":
            self.memory.delete_notes(arg or None)
            print(c("green", f"  ✓ {'Notes for ' + arg + ' deleted' if arg else 'All notes deleted'}."))
            return True

        if slug == "/save":
            fname = arg.strip() or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            if not fname.endswith(".md"):
                fname += ".md"
            md   = self.memory.export_session(fname.replace(".md",""))
            path = os.path.join(self.cwd, fname)
            with open(path, "w") as fh:
                fh.write(md)
            print(c("green", f"  ✓ Session saved to {path}"))
            return True

        if slug == "/dryrun":
            self.dry_run = not self.dry_run
            state = (c("yellow", "ON  — preview only") if self.dry_run
                     else c("green", "OFF — commands execute normally"))
            print(c("cyan", f"  ⚡ Dry-run: {state}"))
            return True

        # ── /config — open MCP config in editor ───────────
        if slug == "/config":
            self._cmd_config()
            return True

        # ── /mcp dispatch ──────────────────────────────────
        if slug == "/mcp":
            return self._handle_mcp_slash(arg)

        if slug == "/telegram":
            return self._handle_telegram(arg)

        if slug == "/exit":
            print(c("cyan", "\n  Goodbye.\n"))
            sys.exit(0)

        return False

    # ══════════════════════════════════════════════════════
    # /config  — open ~/.hackers_ai_mcp.json in editor
    # ══════════════════════════════════════════════════════
    def _cmd_config(self):
        _mcp_config_ensure()
        print(c("cyan", f"\n  MCP config: {MCP_CONFIG_PATH}"))
        print(c("dim",   "  Format (Claude Desktop style):"))
        print(c("dim", textwrap.dedent("""
  {
    "mcpServers": {
      "my-server": {
        "command": "/usr/bin/python3",
        "args": ["/path/to/mcp_server.py", "--flag", "value"],
        "env": { "KEY": "VALUE" }
      },
      "another-server": {
        "command": "node",
        "args": ["/path/to/server.js"]
      }
    }
  }
        """).rstrip()))
        print()
        print(c("dim", "  Opening editor... (save & close to return)"))
        _mcp_config_open_editor()

        # After editor closes, reload and show what's configured
        cfg     = _mcp_config_load()
        servers = cfg.get("mcpServers", {})
        if servers:
            print(c("green", f"\n  ✓ Config loaded — {len(servers)} server(s) defined:"))
            for name, scfg in servers.items():
                cmd_str = scfg.get("command", "?") + " " + " ".join(scfg.get("args", []))
                print(c("dim", f"    · {c('cyan', name):<20} {cmd_str[:60]}"))
            print(c("dim", "\n  Use /mcp use <name> to connect to a server."))
        else:
            print(c("yellow", "  No servers defined yet. Edit the config file to add some."))
        print()

    # ══════════════════════════════════════════════════════
    # /mcp subcommands
    # ══════════════════════════════════════════════════════
    def _handle_mcp_slash(self, arg: str) -> bool:
        parts   = arg.strip().split(None, 2)
        sub_cmd = parts[0].lower() if parts else "list"
        sub1    = parts[1].strip() if len(parts) > 1 else ""
        sub2    = parts[2].strip() if len(parts) > 2 else ""

        # ── list ──────────────────────────────────────────
        if sub_cmd in ("list", "ls", ""):
            cfg     = _mcp_config_load()
            servers = cfg.get("mcpServers", {})
            active  = self.memory.get_mcp_active_name()
            if not servers:
                print(c("dim",  "\n  No MCP servers configured."))
                print(c("dim",  "  Run /config to open the config file and add servers."))
                print()
                return True
            print()
            print(c("cyan", "  ╔══ MCP SERVERS (from ~/.hackers_ai_mcp.json) " + "═"*18))
            for name, scfg in servers.items():
                act     = c("green", " ● ACTIVE") if name == active else c("dim", " ○")
                cmd_str = scfg.get("command", "?")
                args    = scfg.get("args", [])
                args_str = " ".join(str(a) for a in args[:3])
                if len(args) > 3:
                    args_str += " ..."
                env_keys = list((scfg.get("env") or {}).keys())
                env_str  = f"  env:[{','.join(env_keys)}]" if env_keys else ""
                print(c("cyan", "  ║ ") +
                      f"{c('white', name):<20} {cmd_str} {args_str[:40]}{env_str}{act}")
            print(c("cyan", "  ╚" + "═"*62))
            print(c("dim",  "  /mcp use <name>     — connect to a server"))
            print(c("dim",  "  /config              — edit config file"))
            print()
            return True

        # ── reload ────────────────────────────────────────
        if sub_cmd == "reload":
            active_name = self.memory.get_mcp_active_name()
            if not active_name:
                print(c("yellow", "  No active server to reload. Use /mcp use <name> first."))
                return True
            cfg     = _mcp_config_load()
            servers = cfg.get("mcpServers", {})
            if active_name not in servers:
                print(c("red", f"  '{active_name}' no longer in config. Run /config to add it back."))
                self._mcp_client = None
                self.memory.clear_mcp_active()
                return True
            if self._mcp_client:
                self._mcp_client._stop()
            srv_cfg = servers[active_name]
            self._mcp_client = _mcp_client_from_config(active_name, srv_cfg)
            print(c("dim", f"  Reconnecting to '{active_name}'..."), end="", flush=True)
            try:
                self._mcp_client.initialize()
                tools = self._mcp_client.list_tools(force_refresh=True)
                print(c("green", f" ✓ {len(tools)} tools"))
            except Exception as e:
                print(c("red", f" ✗ {e}"))
                self._mcp_client = None
            return True

        # ── use <name> ────────────────────────────────────
        if sub_cmd == "use":
            if not sub1:
                print(c("red", "  Usage: /mcp use <server-name>"))
                print(c("dim", "  Run /mcp list to see available servers."))
                return True
            cfg     = _mcp_config_load()
            servers = cfg.get("mcpServers", {})
            if sub1 not in servers:
                print(c("red", f"  Server '{sub1}' not found in config."))
                print(c("dim",  "  Run /mcp list to see configured servers, or /config to add one."))
                return True
            # Stop previous client if running
            if self._mcp_client:
                self._mcp_client._stop()
            srv_cfg          = servers[sub1]
            self._mcp_client = _mcp_client_from_config(sub1, srv_cfg)
            print(c("dim", f"  Starting '{sub1}'..."), end="", flush=True)
            try:
                self._mcp_client.initialize()
            except Exception as e:
                print(c("red", f" ✗ Could not start server: {e}"))
                self._mcp_client = None
                return True
            tools = self._mcp_client.list_tools(force_refresh=True)
            print(c("green", f" ✓ Connected — {len(tools)} tools"))
            self.memory.set_mcp_active(sub1)
            if tools:
                print()
                print(c("cyan", f"  ╔══ {sub1.upper()} TOOLS " + "═"*48))
                for t in tools[:25]:
                    schema = t.get("inputSchema", {}) or {}
                    props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                    params = ", ".join(props.keys()) if props else ""
                    desc   = t.get("description", "")[:55]
                    print(c("cyan", "  ║ ") + c("white", f"{t['name']:<22}") +
                          c("dim", f"({params})") + c("dim", f"  {desc}"))
                if len(tools) > 25:
                    print(c("dim", f"  ║  ... +{len(tools)-25} more  (/mcp tools for full list)"))
                print(c("cyan", "  ╚" + "═"*62))
            resources = self._mcp_client.list_resources()
            if resources:
                print(c("dim", f"\n  Resources ({len(resources)}):"))
                for r in resources[:8]:
                    print(c("dim", f"    · {r.get('uri','?')}  {r.get('name','')[:50]}"))
            print()
            return True

        # ── stop / disconnect ─────────────────────────────
        if sub_cmd in ("stop", "disconnect", "off", "exit", "quit"):
            if self._mcp_client:
                name = self._mcp_client.name
                self._mcp_client._stop()
                self._mcp_client = None
                self.memory.clear_mcp_active()
                print(c("green", f"  ✓ Disconnected from '{name}'"))
            else:
                print(c("dim", "  No active MCP connection."))
            return True

        # ── tools [name] ──────────────────────────────────
        if sub_cmd == "tools":
            client = self._mcp_client
            if sub1:
                # Show tools for a named server without switching active
                cfg     = _mcp_config_load()
                servers = cfg.get("mcpServers", {})
                if sub1 not in servers:
                    print(c("red", f"  Server '{sub1}' not found in config."))
                    return True
                client = _mcp_client_from_config(sub1, servers[sub1])
                print(c("dim", f"  Connecting to '{sub1}' for tool listing..."), end="", flush=True)
                try:
                    client.initialize()
                    print(c("green", " ok"))
                except Exception as e:
                    print(c("red", f" failed: {e}"))
                    return True
            if not client:
                print(c("yellow", "  No active MCP. Use /mcp use <name> first, or /mcp tools <name>."))
                return True
            tools = client.list_tools(force_refresh=True)
            if not tools:
                print(c("yellow", "  No tools returned by this server."))
                return True
            srv_name = client.name
            print()
            print(c("cyan", f"  ╔══ {srv_name.upper()} — {len(tools)} TOOLS " + "═"*35))
            for t in tools:
                schema = t.get("inputSchema", {}) or {}
                props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                req    = schema.get("required", []) if isinstance(schema, dict) else []
                params_parts = []
                for pname, pinfo in (props.items() if isinstance(props, dict) else []):
                    star  = "*" if pname in req else ""
                    ptype = pinfo.get("type", "any") if isinstance(pinfo, dict) else "any"
                    params_parts.append(f"{pname}{star}:{ptype}")
                params_str = ", ".join(params_parts) or "—"
                print(c("cyan", "  ║ ") + c("white", t["name"]))
                print(c("cyan", "  ║   ") + c("dim", f"{t.get('description','')[:75]}"))
                print(c("cyan", "  ║   ") + c("dim", f"params: {params_str}"))
            print(c("cyan", "  ╚" + "═"*62))
            print()
            return True

        # ── call <tool> [json_args] ───────────────────────
        if sub_cmd == "call":
            if not sub1:
                print(c("red",  "  Usage: /mcp call <tool_name> [json_args]"))
                print(c("dim",  '  Example: /mcp call decompile_function {"name":"main"}'))
                return True
            if not self._mcp_client:
                print(c("yellow", "  No active MCP. Use /mcp use <name> first."))
                return True
            tool_name = sub1
            args_raw  = sub2 or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"input": args_raw} if args_raw else {}
            print(c("cyan", f"\n  [MCP] {self._mcp_client.name}.{tool_name}({args_raw[:80]})"))
            try:
                result = self._mcp_client.call_tool(tool_name, args, timeout=60)
                text   = MCPStdioClient.extract_text_result(result)
                is_err = result.get("isError", False)
                self.memory.log_mcp_call(
                    self._mcp_client.name, tool_name, args_raw, text[:500],
                    "error" if is_err else "success"
                )
                if is_err:
                    print(c("red", "  ✗ Tool error:"))
                    print(c("red", text[:800]))
                else:
                    print(c("green", "  ✓ Result:"))
                    print(c("dim", "  ┌" + "─"*61))
                    for line in (text or "(empty)").splitlines()[:80]:
                        print(c("dim", "  │ ") + line)
                    extra = text.count("\n") - 80
                    if extra > 0:
                        print(c("dim", f"  │ ... ({extra} more lines)"))
                    print(c("dim", "  └" + "─"*61))
                print()
            except Exception as e:
                print(c("red", f"  [MCP] Error: {e}"))
            return True

        # ── ai <natural language task> ────────────────────
        if sub_cmd == "ai":
            task = (sub1 + " " + sub2).strip()
            if not task:
                print(c("red", "  Usage: /mcp ai <describe what you want>"))
                return True
            if not self._mcp_client:
                print(c("yellow", "  No active MCP. Use /mcp use <name> first."))
                return True
            tools     = self._mcp_client.list_tools()
            tools_ctx = self._mcp_client.format_tools_for_prompt()
            print(c("dim", f"  [MCP/AI] Planning with {len(tools)} tools from {self._mcp_client.name}..."))
            prompt = textwrap.dedent(f"""
You control MCP server '{self._mcp_client.name}' with these tools:

{tools_ctx}

User task: {task}

For each tool call needed, output one JSON object per line (no other text):
{{"tool": "<name>", "args": {{<arguments>}}, "reason": "<why>"}}

Output ONLY JSON lines. No explanation, no markdown.
            """).strip()
            agent      = FreeLLM(model=self.model)
            raw        = agent.ask(prompt).strip()
            calls_made = 0
            all_results = []
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    call = json.loads(line)
                except Exception:
                    continue
                t_name = call.get("tool", "")
                t_args = call.get("args", {})
                reason = call.get("reason", "")
                if not t_name:
                    continue
                print(c("cyan", f"\n  [MCP] → {t_name}  ({reason[:60]})"))
                try:
                    result = self._mcp_client.call_tool(t_name, t_args, timeout=60)
                    text   = MCPStdioClient.extract_text_result(result)
                    is_err = result.get("isError", False)
                    self.memory.log_mcp_call(
                        self._mcp_client.name, t_name, json.dumps(t_args),
                        text[:500], "error" if is_err else "success"
                    )
                    icon = c("red", "✗") if is_err else c("green", "✓")
                    print(f"  {icon} {text[:200]}")
                    all_results.append(f"[{t_name}]: {text[:600]}")
                    calls_made += 1
                except Exception as e:
                    print(c("red", f"  ✗ {e}"))

            if calls_made == 0:
                print(c("yellow", "  No tools were invoked. Try being more specific."))
            elif all_results:
                print(c("dim", "\n  [MCP/AI] Summarizing results..."))
                summary_prompt = (
                    f"Task was: {task}\n\n"
                    "Tool results:\n" + "\n".join(all_results[:5]) + "\n\n"
                    "Provide a short, accurate summary. Be concise."
                )
                summary = agent.ask(summary_prompt).strip()
                print()
                print(c("green", "  ╭─ MCP Result Summary " + "─"*40))
                for line in summary.splitlines():
                    print(c("dim", "  │ ") + line)
                print(c("green", "  ╰" + "─"*61))
                print()
            return True

        # ── Unknown subcommand ────────────────────────────
        print(c("red", f"  Unknown /mcp subcommand: '{sub_cmd}'"))
        print(c("dim", "  Commands: list | use <name> | stop | reload"))
        print(c("dim", "            tools [name] | call <tool> [args] | ai <task>"))
        print(c("dim", "  Config  : /config  (edit ~/.hackers_ai_mcp.json)"))
        return True

    def _handle_telegram(self, arg: str) -> bool:
        """
        /telegram --api-token <token> --user-id <id>  — configure & start bot
        /telegram start                               — start poll loop
        /telegram stop                                — stop poll loop
        /telegram status                              — show current status
        /telegram test                                — send a test message
        """
        parts = arg.strip().split()
        sub   = parts[0].lower() if parts else "status"

        # ── status ────────────────────────────────────────────
        if sub == "status" or not parts:
            print()
            cfg = _tg_config_load()
            if not cfg.get("token"):
                print(c("yellow", "  Telegram bot not configured."))
                print(c("dim",    "  Setup: /telegram --api-token <token> --user-id <id>"))
                print(c("dim",    "  Get a token : message @BotFather → /newbot"))
                print(c("dim",    "  Get your ID : message @userinfobot"))
            else:
                running = self._tg._thread and self._tg._thread.is_alive()
                state   = c("green","RUNNING") if running else c("yellow","STOPPED")
                uid     = cfg.get("user_id","?")
                tok     = cfg.get("token","")[:10] + "..." + cfg.get("token","")[-4:]
                print(c("cyan", "  ╔══ TELEGRAM BOT ══════════════════════════════════"))
                print(c("cyan", f"  ║  State   : {state}"))
                print(c("cyan", f"  ║  Token   : {tok}"))
                print(c("cyan", f"  ║  User ID : {uid}"))
                print(c("cyan",  "  ║"))
                print(c("cyan",  "  ║  Send any message to your bot on Telegram and"))
                print(c("cyan",  "  ║  Hackers AI will execute it and reply with output."))
                print(c("cyan",  "  ╚" + "═"*50))
            print()
            return True

        # ── start ─────────────────────────────────────────────
        if sub == "start":
            if not self._tg.token:
                print(c("red", "  Not configured. Run /telegram --api-token T --user-id U first."))
                return True
            if self._tg._thread and self._tg._thread.is_alive():
                print(c("yellow", "  Bot is already running."))
                return True
            self._tg.start(self)
            print(c("green", "  ✓ Telegram bot started — send a message to your bot to control Hackers AI remotely."))
            return True

        # ── stop ──────────────────────────────────────────────
        if sub == "stop":
            self._tg.stop()
            self._tg.disable()
            print(c("yellow", "  ✓ Telegram bot stopped."))
            return True

        # ── test ──────────────────────────────────────────────
        if sub == "test":
            if not self._tg.token:
                print(c("red", "  Not configured. Run /telegram --api-token T --user-id U first."))
                return True
            print(c("dim", "  Sending test message..."), end="", flush=True)
            ok, err = self._tg.send(
                "🤖 <b>Hackers AI — Test Message</b>\n"
                "Connection is working! You can now send commands here.\n\n"
                "<i>Try sending:</i> <code>whoami</code>"
            )
            print(c("green", " ✓ Delivered") if ok else c("red", f" ✗ {err}"))
            return True

        # ── --api-token T --user-id U  (configure + auto-start) ──
        token   = ""
        user_id = ""
        i = 0
        while i < len(parts):
            if parts[i] in ("--api-token", "--token") and i + 1 < len(parts):
                token = parts[i + 1]; i += 2
            elif parts[i] in ("--user-id", "--userid", "--chat-id") and i + 1 < len(parts):
                user_id = parts[i + 1]; i += 2
            else:
                i += 1

        if not token or not user_id:
            print(c("red", "  Usage: /telegram --api-token <token> --user-id <user_id>"))
            print(c("dim", "  Commands: status | start | stop | test"))
            return True

        print(c("dim", f"  Configuring bot (user_id={user_id})..."), end="", flush=True)
        ok, err = self._tg.configure(token, user_id)
        if not ok:
            print(c("red", f" ✗ {err}"))
            print(c("dim",  "  Check your token and user_id are correct."))
            return True

        print(c("green", " ✓ Bot connected!"))
        print(c("dim",   f"  Config saved → {TELEGRAM_CONFIG_PATH}"))

        # Auto-start the poll loop
        self._tg.start(self)
        print(c("green",  "  ✓ Bot is now RUNNING — send any message to your Telegram bot."))
        print(c("dim",    "  Commands you send there are executed here and output is sent back."))
        print(c("dim",    "  Stop with: /telegram stop"))
        return True

    def _confirm_scope(self, host: str) -> bool:
        print()
        print(c("yellow", "  ╔══ SCOPE CONFIRMATION ══════════════════════════════════"))
        print(c("yellow", f"  ║  Target: {c('white', host)}"))
        print(c("yellow",  "  ║  This target is not local and not in your authorized list."))
        print(c("yellow",  "  ║"))
        print(c("yellow",  "  ║  Only test systems you OWN or have WRITTEN PERMISSION to test."))
        print(c("yellow",  "  ║  Unauthorized testing is illegal."))
        print(c("yellow",  "  ║"))
        print(c("yellow",  "  ║  Options:"))
        print(c("yellow",  "  ║    y = proceed this time (not saved)"))
        print(c("yellow", f"  ║    a = proceed and add '{host}' to authorized list"))
        print(c("yellow",  "  ║    n = cancel (default)"))
        print(c("yellow",  "  ╚" + "═"*62))
        try:
            ans = input(c("cyan", "  I confirm I have authorization [y/a/N]: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans == "a":
            self.memory.add_authorized_target(host)
            print(c("green", f"  ✓ {host} added to authorized targets."))
            return True
        elif ans == "y":
            return True
        else:
            print(c("red", "  ✗ Task cancelled."))
            return False

    def _confirm_plan(self, plan: dict) -> bool:
        # ── Telegram mode: send plan as inline Y/N keyboard ────
        if getattr(self, "_tg_mode", False) and self._tg and self._tg.token:
            import uuid

            # Flush any buffered stdout lines (Analysing/Planning status) BEFORE
            # sending the keyboard — so the user sees the status first, then the plan.
            flush_fn = getattr(self._tg, "_force_flush", None)
            if flush_fn:
                flush_fn()
            # Small sleep so the tee-stream write thread drains before we proceed
            time.sleep(0.05)

            cb_id = uuid.uuid4().hex[:12]
            evt   = threading.Event()
            self._tg._pending_confirms[cb_id] = {"event": evt, "result": False}

            # Build step list for Telegram — show description + first 10 words of command
            steps_text = ""
            for s in plan.get("steps", []):
                stype = s.get("type", "command").upper()
                desc  = s.get("description") or ""
                cmd   = s.get("command") or ""
                # Show description as the label; append a short command preview if available
                if cmd and cmd.strip():
                    words     = cmd.split()
                    cmd_short = " ".join(words[:10]) + ("..." if len(words) > 10 else "")
                else:
                    cmd_short = ""
                label = desc[:60] if desc else cmd_short[:60]
                if cmd_short and desc:
                    label += "  ▸ " + cmd_short[:40]
                icon  = "⚙" if stype == "COMMAND" else "🐍" if stype == "PYTHON" else "🔌"
                steps_text += "  " + icon + " [" + stype + "] " + label + "\n"

            warn = plan.get("warning")
            extras = ""
            if self.sticky_target:
                extras += "\n<b>Target:</b> " + self.sticky_target
            if plan.get("requires_root"):
                extras += "\n🔴 <b>Requires root</b>"
            if warn and str(warn).lower() not in ("null", "none", ""):
                extras += "\n⚠️ <b>Warning:</b> " + str(warn)

            tg_msg = (
                "📋 <b>EXECUTION PLAN</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<b>Summary:</b> " + plan.get("summary", "N/A") + "\n"
                "<b>Steps:</b> "   + str(len(plan.get("steps", [])))
                + extras + "\n\n"
                "<pre>" + steps_text.rstrip() + "</pre>\n\n"
                "Execute this plan?"
            )

            msg_id = self._tg.send_with_keyboard(tg_msg, cb_id)
            if not msg_id:
                # Message failed to send (e.g. HTML parse error after retry)
                # Send a minimal plain-text fallback so the user can still confirm
                fallback = (
                    "📋 EXECUTION PLAN\n"
                    + plan.get("summary", "N/A") + "\n\n"
                    + str(len(plan.get("steps", []))) + " step(s) ready.\n"
                    "Execute this plan?"
                )
                msg_id = self._tg.send_with_keyboard(fallback, cb_id)

            tapped = evt.wait(timeout=120)
            result = self._tg._pending_confirms.pop(cb_id, {}).get("result", False)

            if not tapped:
                if msg_id:
                    self._tg._edit_reply_markup(msg_id, "⏱ Timed out — task cancelled.")
                return False

            label_chosen = "✅ Confirmed — executing..." if result else "❌ Cancelled."
            if msg_id:
                self._tg._edit_reply_markup(msg_id, label_chosen)
            return result

        # ── Terminal mode ──────────────────────────────────────
        print()
        w = c("yellow", "║")
        print(c("yellow", "  ╔══ EXECUTION PLAN " + "═"*43))
        print(f"  {w}  {c('white','Summary')} : {plan.get('summary','N/A')}")
        print(f"  {w}  {c('white','Steps  ')} : {len(plan.get('steps', []))}")
        if self.sticky_target:
            print(f"  {w}  {c('cyan','Target ')} : {self.sticky_target}")
        if plan.get("requires_root"):
            print(f"  {w}  {c('red','⚠ Requires root')}")
        warn = plan.get("warning")
        if warn and str(warn).lower() not in ("null","none",""):
            print(f"  {w}  {c('red','⚡ WARNING')}: {warn}")
        print(c("yellow", f"  ╠══ STEPS {chr(9552)*53}"))
        for s in plan.get("steps", []):
            stype = s.get("type","command").upper()
            label = (s.get("command") or s.get("description") or "")[:82]
            tc    = "cyan" if stype == "COMMAND" else "magenta" if stype == "PYTHON" else "dim"
            print(f"  {w}  [{c(tc, f'{stype:<8}')}] {label}")
        print(c("yellow", f"  {chr(9562)}{chr(9552)*62}"))
        print()

        try:
            ans = input(c("cyan", "  Execute? [Y/n]: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        return ans in ("", "y", "yes")

    def _print_response(self, text: str):
        # In Telegram mode the stdout tee will forward the text directly —
        # skip the box-drawing chrome which renders poorly in Telegram.
        if getattr(self, "_tg_mode", False):
            # Send summary directly via bot API to guarantee delivery,
            # bypassing the tee buffer which may not flush single-line output.
            if self._tg and self._tg.enabled:
                self._tg.send(f"📋 <b>Summary:</b>\n<pre>{text[:3000]}</pre>")
            else:
                print(text)
            return
        print()
        print(c("green", "  ╭─ Hackers AI ") + c("dim", "─"*49))
        for line in text.splitlines():
            print(c("dim", "  │ ") + line)
        print(c("green", "  ╰" + "─"*62))
        print()

    def _inject_profile_context(self):
        self.profile["cwd"] = self.cwd
        _su = os.environ.get("SUDO_USER")
        self.profile["real_home"] = (os.path.expanduser(f"~{_su}") if _su
                                     else os.path.expanduser("~"))
        self.profile["sticky_target"] = self.sticky_target or "(none set)"
        # Inject active MCP tools + full per-tool schema into profile for planner
        if self._mcp_client:
            tools = self._mcp_client.list_tools()
            tool_names = [t.get("name","") for t in tools]
            self.profile["active_mcp_tools"]          = self._mcp_client.format_tools_for_prompt()
            self.profile["active_mcp_name"]           = self._mcp_client.name
            self.profile["active_mcp_schema"]         = CLI._build_mcp_schema_text(self._mcp_client)
            self.profile["active_mcp_has_exec"]       = "execute_command" in tool_names
            self.profile["active_mcp_exec_tool"]      = (
                "execute_command" if "execute_command" in tool_names else ""
            )
        else:
            self.profile["active_mcp_tools"]          = ""
            self.profile["active_mcp_name"]           = ""
            self.profile["active_mcp_schema"]         = ""
            self.profile["active_mcp_has_exec"]       = False
            self.profile["active_mcp_exec_tool"]      = ""

    @staticmethod
    def _build_mcp_schema_text(client) -> str:
        """
        Verbose tool catalogue with per-param descriptions so the LLM
        picks exact arg names on the first try.
        Format per tool:
          tool_name(param*(type) — param desc, ...) — tool desc
        """
        tools = client.list_tools()
        if not tools:
            return "(no tools)"
        lines = []
        for t in tools:
            name   = t.get("name", "?")
            tdesc  = (t.get("description") or "").replace("\n", " ").strip()[:90]
            schema = t.get("inputSchema") or {}
            props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
            req    = set(schema.get("required", [])) if isinstance(schema, dict) else set()
            params = []
            for pname, pinfo in (props.items() if isinstance(props, dict) else []):
                star  = "*" if pname in req else ""
                if isinstance(pinfo, dict):
                    ptype = pinfo.get("type", "any")
                    if pinfo.get("enum"):
                        ptype = "|".join(str(e) for e in pinfo["enum"][:5])
                    pdesc = pinfo.get("description", "").replace("\n"," ").strip()[:50]
                else:
                    ptype = "any"
                    pdesc = ""
                hint = f"{pname}{star}({ptype})"
                if pdesc:
                    hint += f"={pdesc}"
                params.append(hint)
            param_str = ", ".join(params) if params else "no params"
            lines.append(f"  {name}({param_str})")
            lines.append(f"    → {tdesc}")
        return "\n".join(lines)

    def process(self, user_input: str):
        self._inject_profile_context()

        enriched_input = user_input
        if self.sticky_target and not re.search(
            r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|https?://|\b[a-z0-9-]+\.[a-z]{2,}\b',
            user_input, re.IGNORECASE
        ):
            needs_target = any(kw in user_input.lower() for kw in [
                "scan","recon","enumerate","fuzz","brute","nikto","nmap",
                "gobuster","sqlmap","nuclei","whatweb","test","probe","xss","sqli",
            ])
            if needs_target:
                enriched_input = f"{user_input} target: {self.sticky_target}"

        allowed, reason, host = self.scope_guard.check(enriched_input, self.sticky_target)
        if allowed is None:
            if not self._confirm_scope(host):
                return

        history = self.memory.get_history(MAX_HISTORY)

        # ── Single unified analysis: intent + context + history in one JSON call ──
        print(c("dim", "\n  [→] Analysing..."))
        analyzer = QueryAnalyzer(model=self.model)
        analysis = analyzer.analyze(enriched_input, history)

        intent   = analysis.get("intent", "task")
        ready    = analysis.get("ready", True)
        enriched = analysis.get("enriched_task") or enriched_input
        question = analysis.get("question")
        found_in = analysis.get("found_in", "task")

        # ── Informational: answer directly, do NOT send to Telegram ─────────
        if intent == "informational":
            print(c("dim", "  [→] Informational query"))
            gen      = ResponseGenerator(model=self.model)
            response = gen.ask(enriched, history, self.profile)
            if getattr(self, "_tg_mode", False):
                # Send only to Telegram as a plain info message — no task notification
                self._tg.send(f"ℹ️ <b>Info:</b>\n<pre>{response[:3000]}</pre>")
            else:
                self._print_response(response)
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", response,   self.model)
            return

        # ── Need more info: ask the user ─────────────────────────────────────
        if not ready:
            if getattr(self, "_tg_mode", False) and self._tg and self._tg.token:
                self._tg.send(f"❓ <b>Need more info:</b>\n{question}")
            print()
            print(c("yellow", "  ╭─ Need more info " + "─"*44))
            print(c("yellow", f"  │  {question}"))
            print(c("yellow", "  ╰" + "─"*61))
            print()
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", f"[Awaiting: {question}]", self.model)
            return

        # ── Task: show how context was resolved ──────────────────────────────
        if found_in == "history":
            print(c("cyan", f"  [✓] Context from history → {enriched[:80]}"))
        else:
            print(c("dim", "  [✓] Context resolved"))

        print(c("dim", "  [→] Planning..."))
        planner = PlannerEngine()
        plan    = planner.plan(enriched, history, self.profile, self.model)

        if not plan:
            print(c("yellow", "  [!] Planner failed — direct response."))
            gen      = ResponseGenerator(model=self.model)
            response = gen.ask(enriched, history, self.profile)
            self._print_response(response)
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", response,   self.model)
            return

        if plan.get("intent") == "informational" and not plan.get("steps"):
            info = plan.get("summary", "")
            self._print_response(info)
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", info,       self.model)
            return

        if self.dry_run:
            print(c("yellow", "\n  ⚡ DRY-RUN — showing plan only.\n"))
            self._confirm_plan(plan)
            return

        if not self._confirm_plan(plan):
            print(c("red", "\n  ✗ Task aborted.\n"))
            return

        task_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
        dyn_engine = DynamicExecutionEngine(self.memory, model=self.model)
        dyn_engine.engine._cwd               = self.cwd
        dyn_engine.engine._user_input        = enriched
        dyn_engine.engine._mcp_client        = self._mcp_client
        dyn_engine.engine._current_step_args = {}
        dyn_engine.engine._tg_mode           = getattr(self, "_tg_mode", False)
        _task_start = time.time()
        raw         = dyn_engine.run(enriched, plan, task_id, self.profile)

        print(c("dim", "\n  [→] Summarizing..."))
        _task_end = time.time()
        summary = Summarizer(model=self.model).summarize(raw, user_input, history)
        self._print_response(summary)
        self.memory.add_message("user",      user_input, self.model)
        self.memory.add_message("assistant", summary,    self.model)

        # Telegram task-done notification (tasks only — informational handled above)
        _elapsed = _task_end - _task_start if "_task_start" in locals() else 0
        if not getattr(self, "_tg_mode", False):
            self._tg.notify_task_done(user_input, summary, _elapsed)

        # _suggest_next is terminal-only — skip in Telegram mode
        if not getattr(self, "_tg_mode", False) and len(enriched.split()) > 3:
            self._suggest_next(user_input, summary)

    def _suggest_next(self, task: str, result_summary: str):
        try:
            prompt = (
                "Suggest 2-3 SHORT follow-up commands/questions for a Linux/pentest agent.\n"
                "Rules:\n"
                "- Each on its own line, no bullets, no numbering\n"
                "- Under 60 chars each\n"
                "- Only suggest if contextually relevant\n"
                "- Trivial tasks (whoami, ls) → return empty string\n\n"
                f"Task: {task}\nResult: {result_summary[:400]}"
            )
            agent = FreeLLM(model=self.model)
            raw   = agent.ask(prompt).strip()
            lines = [l.strip() for l in raw.splitlines()
                     if l.strip() and len(l.strip()) > 5][:3]
            if lines:
                print(c("dim", "  ╭─ Suggested next ─" + "─"*43))
                for i, s in enumerate(lines, 1):
                    print(c("dim","  │ ") + c("cyan", f"  {i}.") + f" {s}")
                print(c("dim", "  ╰" + "─"*61))
                print()
        except Exception:
            pass

    def run(self):
        self._print_banner()

        # Known shell commands — used only to qualify _cmd_only check below.
        # These must be a single recognisable binary name with flag/path args only.
        _DIRECT_CMDS = {
            "ls","ll","la","l","pwd","cat","head","tail","less","more",
            "echo","ps","top","htop","df","du","free","uname","whoami",
            "id","env","printenv","date","uptime","w","who","last",
            "find","grep","awk","sed","cut","sort","uniq","wc","tr",
            "mkdir","rmdir","rm","cp","mv","touch","chmod","chown","ln",
            "which","whereis","type","file","stat","lsof","netstat","ss",
            "ip","ifconfig","route","arp","ping","traceroute","nslookup",
            "dig","host","curl","wget","tree","lsblk","mount","umount",
            "systemctl","service","journalctl","dmesg","lscpu",
            "tar","zip","unzip","gzip","gunzip","nc","ncat","ssh","scp",
            "nano","vim","vi","nvim","python3","python","node","php",
            "ruby","bash","sh","zsh","clear","reset","tee",
            "base64","xxd","hexdump","strings","objdump","readelf",
            "strace","ltrace","ldd","nm","diff","patch",
            "nmap","sqlmap","hydra","gobuster","ffuf","wfuzz","nikto",
            "aircrack-ng","hashcat","john","msfconsole","msfvenom",
        }

        _NAT_CMDS = {"recon","note","notes","save","dryrun","target","shell","auth","mcp","config"}

        while True:
            try:
                user_input = input(self._get_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print(c("cyan", "\n\n  Goodbye.\n"))
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                if not self._handle_slash(user_input):
                    print(c("red", f"  Unknown command: {user_input}. Type /help."))
                continue

            _words = user_input.strip().split()
            _first = _words[0].lower() if _words else ""

            if _first in _NAT_CMDS:
                self._handle_slash("/" + user_input.strip())
                continue

            if _first == "cd":
                target = " ".join(_words[1:]).strip() if len(_words) > 1 else ""
                if not target or target == "~":
                    _su = os.environ.get("SUDO_USER")
                    target = os.path.expanduser(f"~{_su}") if _su else os.path.expanduser("~")
                elif target.startswith("~/"):
                    _su = os.environ.get("SUDO_USER")
                    home = os.path.expanduser(f"~{_su}") if _su else os.path.expanduser("~")
                    target = os.path.join(home, target[2:])
                elif not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(self.cwd, target))
                if os.path.isdir(target):
                    self.cwd = os.path.realpath(target)
                    print(c("green", f"  ✓ {self.cwd}"))
                else:
                    print(c("red", f"  cd: {target}: No such directory"))
                continue

            # ── Direct shell execution rules ───────────────────────
            # Rule 1: unambiguous shell syntax → always direct
            _shell_syntax = (
                "|"  in user_input
                or ">>" in user_input
                or ">"  in user_input
                or "<"  in user_input
                or user_input.strip().startswith("./")
                or user_input.strip().startswith("sudo ")
            )

            # Rule 2: known command where every token is a flag, path, number,
            # or a bare word used as a flag-value (right after a flag like -name).
            # Any standalone plain English word → NL instruction, not a shell cmd.
            def _is_shell_invocation(words):
                prev_was_flag = False
                for tok in words[1:]:
                    if tok.startswith("-"):
                        prev_was_flag = True
                        continue
                    if prev_was_flag:          # bare word as flag-value: -name passwd
                        prev_was_flag = False
                        continue
                    prev_was_flag = False
                    # standalone token: must look like a path / number / glob
                    if tok.startswith("/"):    continue
                    if tok.startswith("~"):    continue
                    if tok.startswith("$"):    continue
                    if tok.startswith("."):    continue
                    if re.match(r"^\d", tok): continue
                    if re.match(r"^[*?]", tok):continue
                    if "=" in tok:             continue
                    if "/" in tok or "." in tok: continue
                    return False   # plain English word → treat as instruction
                return True

            _cmd_only = (
                _first in _DIRECT_CMDS
                and len(_words) <= 8
                and _is_shell_invocation(_words)
            )

            _is_direct = _shell_syntax or _cmd_only

            if _is_direct:
                ex   = CommandExecutor()
                _cmd = user_input.strip()
                if _first in self.GUI_COMMANDS or self.run_as_user:
                    _cmd = CommandExecutor.as_user(_cmd)
                res = ex.run(_cmd, timeout=60, cwd=self.cwd)
                self.memory.add_message("user", user_input, self.model)
                self.memory.add_message("assistant",
                    res.get("stdout","") or res.get("stderr",""), self.model)
                continue

            try:
                self.process(user_input)
            except KeyboardInterrupt:
                print(c("yellow", "\n  [⚡] Interrupted. Type /exit to quit.\n"))
            except Exception as e:
                print(c("red", f"\n  [ERROR] {e}"))
                import traceback
                traceback.print_exc()

# ══════════════════════════════════════════════════════════════
# SECTION 20 — ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("\033[33m  [*] Hackers AI requires root. Re-launching with sudo...\033[0m")
        os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        sys.exit(1)

    _cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
    if os.path.isdir(_cache):
        shutil.rmtree(_cache, ignore_errors=True)

    cli = CLI()
    cli.run()
