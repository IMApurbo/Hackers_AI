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

# ── Real user home (works correctly even when running under sudo) ──
def _real_home() -> str:
    """Return the invoking user's home directory even when running as root via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            return pwd.getpwnam(sudo_user).pw_dir
        except Exception:
            pass
    return os.path.expanduser("~")

_REAL_HOME = _real_home()

# ── prompt_toolkit (optional — graceful fallback to input()) ──
try:
    from prompt_toolkit import PromptSession as _PTSession
    from prompt_toolkit.completion import Completer as _PTCompleter, Completion as _PTCompletion
    from prompt_toolkit.styles import Style as _PTStyle
    from prompt_toolkit.formatted_text import ANSI as _PTANSI
    _PT_OK = True
except ImportError:
    _PT_OK = False

# ══════════════════════════════════════════════════════════════
# TERMINAL WIDTH HELPER
# ══════════════════════════════════════════════════════════════
def _tw(indent: int = 2, fallback: int = 80) -> int:
    """Return usable terminal width minus left indent. Min 40."""
    cols = shutil.get_terminal_size(fallback=(fallback, 24)).columns
    return max(40, cols - indent)

def _strip_ansi_g(s: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", s)

def _box_row(content: str, border_char: str = "║", total_width: int = 0,
             prefix: str = "  ", pad_char: str = " ") -> str:
    """Return a box content row padded to total_width with right border.
    total_width = full terminal width (includes prefix+left border).
    The row looks like:  <prefix><border_char> <content...pad> <border_char>
    If total_width==0, calls _tw() automatically."""
    if total_width == 0:
        total_width = _tw() + len(prefix)  # _tw already subtracts indent
    # visible length of content
    vis = len(_strip_ansi_g(content))
    # space available between the two border chars:  total - len(prefix) - 2(borders) - 2(spaces)
    inner = total_width - len(prefix) - 4
    padding = max(0, inner - vis)
    return f"{prefix}{border_char} {content}{pad_char * padding} {border_char}"

def _box_top(title: str, color: str = "cyan", tl: str = "╔", h: str = "═",
             tr: str = "╗", prefix: str = "  ") -> tuple:
    """Return (top_line, bw) where bw is the box width to pass to _box_row and _box_bot.
    top_line looks like:  <prefix><tl><h><h> TITLE <h>...<h><tr>
    bw = _tw() + len(prefix)  — pass this as total_width to _box_row and _box_bot."""
    bw = _tw() + len(prefix)
    # inner width = bw - len(prefix) - 2 (left+right border chars)
    inner = bw - len(prefix) - 2
    label = f"{h}{h} {title} "
    fill  = max(1, inner - len(label))
    line  = f"{prefix}{tl}{label}{h * fill}{tr}"
    return c(color, line), bw

def _box_bot(bw: int, color: str = "cyan", bl: str = "╚", h: str = "═",
             br: str = "╝", prefix: str = "  ") -> str:
    """Return bottom border that exactly matches a top produced by _box_top(bw=bw)."""
    inner = bw - len(prefix) - 2
    return c(color, f"{prefix}{bl}{h * inner}{br}")

def _box_inner(bw: int, prefix: str = "  ") -> int:
    """Usable content width inside a box: bw - len(prefix) - 2(borders) - 2(spaces)."""
    return bw - len(prefix) - 4

def _box_safe(text: str, bw: int, border: str = "║",
              prefix: str = "  ", color: str = "") -> list:
    """Return a list of _box_row strings for `text`, guaranteed to never overflow bw.

    Handles:
    - Strips ANSI for width measurement so colour never counts as width.
    - Splits on embedded newlines first.
    - Hard-wraps any fragment that still exceeds the inner width.
    - Optionally re-applies `color` to each wrapped fragment.
    """
    inner = _box_inner(bw, prefix)
    rows  = []
    for raw_line in text.splitlines():
        clean = _strip_ansi_g(raw_line).rstrip()
        if not clean:
            rows.append(_box_row("", border, bw, prefix))
            continue
        while clean:
            if len(clean) <= inner:
                fragment = c(color, clean) if color else clean
                rows.append(_box_row(fragment, border, bw, prefix))
                break
            # prefer to split at last space before limit; hard-cut if none
            cut = clean.rfind(" ", 0, inner)
            cut = cut if cut > 0 else inner
            fragment = c(color, clean[:cut]) if color else clean[:cut]
            rows.append(_box_row(fragment, border, bw, prefix))
            clean = clean[cut:].lstrip()
    return rows

# ══════════════════════════════════════════════════════════════
# READLINE SETUP — arrow keys, history, cursor movement
# ══════════════════════════════════════════════════════════════
try:
    import readline as _rl
    import atexit as _atexit

    _HIST_FILE = os.path.join(_REAL_HOME, ".hackers_ai_history")
    _rl.set_history_length(1000)

    # Fix ownership if the history file was previously created by root
    # (happens when the script was run with sudo before this fix)
    if os.path.exists(_HIST_FILE) and os.geteuid() == 0:
        _sudo_user = os.environ.get("SUDO_USER")
        if _sudo_user:
            try:
                import pwd as _pwd
                _pw = _pwd.getpwnam(_sudo_user)
                if os.stat(_HIST_FILE).st_uid == 0:  # owned by root — fix it
                    os.chown(_HIST_FILE, _pw.pw_uid, _pw.pw_gid)
            except Exception:
                pass

    try:
        _rl.read_history_file(_HIST_FILE)
    except (FileNotFoundError, PermissionError):
        pass

    def _write_history():
        try:
            _rl.write_history_file(_HIST_FILE)
            # Ensure the file is owned by the real user, not root
            if os.geteuid() == 0:
                _sudo_user = os.environ.get("SUDO_USER")
                if _sudo_user:
                    try:
                        import pwd as _pwd
                        _pw = _pwd.getpwnam(_sudo_user)
                        os.chown(_HIST_FILE, _pw.pw_uid, _pw.pw_gid)
                    except Exception:
                        pass
        except PermissionError:
            pass

    _atexit.register(_write_history)

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
# SECTION 0.5 — USER PROFILE (--improve / -i)
# ══════════════════════════════════════════════════════════════
#
# Stores a compact, deduplicated, ever-growing portrait of the user.
# Each line is a single fact / preference / observed behaviour.
# On every session close and on /improve the AI scans session history,
# extracts new user facts, skips any already captured, appends the rest,
# and displays the full updated profile automatically.
#
# File: ~/.hackers_ai_profile.txt   (one fact per line, UTF-8)
# ──────────────────────────────────────────────────────────────

IMPROVE_PROFILE_PATH = os.path.join(_REAL_HOME, ".hackers_ai_profile.txt")

class UserProfileImprover:
    """
    Reads ~/.hackers_ai_profile.txt, derives new user-facts from the
    current session, deduplicates semantically, and appends only lines
    whose meaning is not already present.

    Each stored line is a short (≤ 120 char) factual statement, e.g.:
      • User prefers to communicate in Bangla.
      • User uses Ghidra for reverse engineering.

    /improve — auto-updates from session history and prints the profile.
    No manual sub-commands needed.
    """

    SEED_FACTS = [
        "User prefers to communicate in Bangla.",
        "User uses Ghidra for reverse engineering and CTF challenges.",
    ]

    def __init__(self, model: str):
        self.model = model

    # ── I/O ────────────────────────────────────────────────────
    def _load(self) -> list:
        if not os.path.exists(IMPROVE_PROFILE_PATH):
            return []
        with open(IMPROVE_PROFILE_PATH, "r", encoding="utf-8") as f:
            return [ln.rstrip() for ln in f if ln.strip()]

    def _save(self, lines: list):
        with open(IMPROVE_PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        # Restrict to owner-only (profile may contain sensitive prefs)
        try:
            os.chmod(IMPROVE_PROFILE_PATH, 0o600)
        except Exception:
            pass

    # ── Core logic ─────────────────────────────────────────────
    def _existing_block(self, lines: list) -> str:
        return "\n".join(f"  {i+1}. {l}" for i, l in enumerate(lines)) if lines else "  (none yet)"

    def _derive_new_facts(self, history_text: str, existing: list) -> list:
        """Ask the LLM to extract new, non-duplicate facts from the session.
        MERGE-FIRST policy: always try to merge new info into an existing line.
        Only add a brand-new line if the topic is completely absent.
        Goal: keep the profile as short and dense as possible."""
        existing_block = self._existing_block(existing)
        prompt = (
            "You are a user-modelling assistant for an AI agent.\n"
            "The conversation history has two roles: 'user' (the human) and 'assistant' (the AI).\n"
            "Your job: extract NEW facts about the USER (the human) from this history.\n\n"
            "IDENTITY RULES:\n"
            "- 'user' role lines = what the human said\n"
            "- 'assistant' role lines = what the AI said — IGNORE for user facts\n"
            "- If the user states their own name, record it as a fact about the user\n"
            "- If the user gives the AI a nickname, record that as a fact too\n"
            "- Never mix up who said what — only extract facts about the human\n\n"
            "MERGE-FIRST RULES (most important):\n"
            "1. Before adding ANY new line, scan every existing fact for related context.\n"
            "2. If ANY existing line covers the same general topic — even partially — you MUST\n"
            "   merge the new detail into that line instead of adding a new one.\n"
            "   Return: {\"replace\": <1-based line number>, \"fact\": \"<merged compact sentence>\"}\n"
            "3. The merged line must be AS SHORT AS POSSIBLE while keeping all meaning.\n"
            "   Combine by listing: e.g. 'User likes RE, software dev, ethical hacking.'\n"
            "   NOT three separate lines for each topic.\n"
            "4. Only add a brand-new line (string) if the topic is COMPLETELY absent from ALL existing facts.\n"
            "5. Each fact ≤ 120 chars, one compact sentence. No filler words.\n"
            "6. Facts must be about the USER (name, preferences, language, skills, tools, habits, goals).\n"
            "7. If nothing new was learned, return [].\n"
            "8. Return ONLY a JSON array — no explanation, no markdown.\n\n"
            "EXAMPLES of good merging:\n"
            "  existing line 2: \"User uses Ghidra for RE and CTF.\"\n"
            "  new info: user also likes software dev and ethical hacking\n"
            "  → {\"replace\": 2, \"fact\": \"User uses Ghidra for RE/CTF; likes software dev and ethical hacking.\"}\n\n"
            "  existing line 1: \"User's name is Alex.\"\n"
            "  new info: user also goes by 'Al'\n"
            "  → {\"replace\": 1, \"fact\": \"User's name is Alex (also Al).\"}\n\n"
            f"EXISTING FACTS:\n{existing_block}\n\n"
            f"CONVERSATION HISTORY (last 6000 chars):\n{history_text[-6000:]}\n\n"
            "Return ONLY a JSON array. Elements:\n"
            "  - {\"replace\": <1-based line number>, \"fact\": \"<merged compact sentence>\"} — PREFERRED\n"
            "  - \"<new fact>\" — ONLY if topic is completely absent from all existing facts\n"
        )
        try:
            agent   = FreeLLM(model=self.model)
            raw     = agent.ask(prompt).strip()
            # Strip optional markdown fences
            raw     = re.sub(r"^```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            parsed  = json.loads(raw)
            if isinstance(parsed, list):
                result = []
                for item in parsed:
                    if isinstance(item, dict) and "replace" in item and "fact" in item:
                        result.append(item)
                    elif isinstance(item, str) and item.strip():
                        result.append(item.strip())
                return result
        except Exception:
            pass
        return []

    def _session_text(self, memory: "MemoryDB") -> str:
        """Flatten permanent cross-session history into a plain string.
        Uses profile_log (never cleared) so facts survive fresh-session resets."""
        rows = memory.get_profile_history(40)   # permanent log, up to 40 turns
        # Try to get names from existing profile for better labelling
        existing = self._load()
        _user_name = "the user"
        _ai_name   = "the AI"
        for line in existing:
            ll = line.lower()
            if "user's name is" in ll or "user name is" in ll:
                m = re.search(r"user'?s? name is (\w+)", ll)
                if m:
                    _user_name = m.group(1).capitalize()
            if "calls the ai" in ll:
                m = re.search(r"calls the ai (\w+)", ll)
                if m:
                    _ai_name = m.group(1).capitalize()
        parts = []
        for row in rows:
            role    = row.get("role", "")
            content = row.get("content", "")
            label = f"USER ({_user_name})" if role == "user" else f"AI ({_ai_name})"
            parts.append(f"[{label}]: {content}")
        return "\n".join(parts)

    # ── Public API ─────────────────────────────────────────────
    def ensure_file(self):
        """Create the profile file with seed facts if it doesn't exist."""
        if not os.path.exists(IMPROVE_PROFILE_PATH):
            self._save(self.SEED_FACTS)
            return True
        return False

    def update(self, memory: "MemoryDB", verbose: bool = True) -> int:
        """
        Derive new/merged facts from session history and update the profile.
        - New facts are appended only if not already covered.
        - Existing facts can be MERGED/REPLACED with richer versions.
        - verbose=False: silent update (used on auto-exit, no output printed).
        Returns the total number of changes (additions + merges).
        """
        self.ensure_file()
        existing = self._load()

        history_text = self._session_text(memory)
        if not history_text.strip():
            if verbose:
                print(c("dim", "  [improve] No session history to learn from yet."))
                self.show()
            return 0

        if verbose:
            print(c("dim", "  [improve] Scanning session for new facts..."), end="", flush=True)
        raw_items = self._derive_new_facts(history_text, existing)
        if verbose:
            print(c("green", " done"))

        updated = list(existing)   # mutable copy
        changes = 0

        for item in raw_items:
            # ── replacement / merge ────────────────────────────────
            if isinstance(item, dict):
                idx = item.get("replace")
                merged = str(item.get("fact", "")).strip()
                if not merged or not isinstance(idx, int):
                    continue
                real_idx = idx - 1   # 1-based → 0-based
                if 0 <= real_idx < len(updated):
                    old_line = updated[real_idx]
                    if merged != old_line:
                        updated[real_idx] = merged
                        if verbose:
                            print(c("yellow", f"  [improve] ↺ Merged line {idx}:"))
                            print(c("dim",    f"    was : {old_line}"))
                            print(c("cyan",   f"    now : {merged}"))
                        changes += 1
                continue

            # ── brand-new fact ─────────────────────────────────────
            fact = str(item).strip()
            if not fact:
                continue
            if self._is_duplicate(fact, updated):
                continue
            updated.append(fact)
            if verbose:
                print(c("cyan", f"  [improve] + New: {fact}"))
            changes += 1

        if changes:
            self._save(updated)
        elif verbose:
            print(c("dim", "  [improve] Nothing new — profile already up to date."))

        if verbose:
            self.show()
        return changes

    def _is_duplicate(self, candidate: str, existing: list) -> bool:
        """Lightweight semantic-ish duplicate check without an LLM call.
        Splits both strings into word sets and checks for high overlap."""
        if not existing:
            return False
        c_words = set(re.sub(r"[^\w]", " ", candidate.lower()).split())
        for line in existing:
            l_words = set(re.sub(r"[^\w]", " ", line.lower()).split())
            if not c_words or not l_words:
                continue
            overlap = len(c_words & l_words) / max(len(c_words), len(l_words))
            if overlap >= 0.65:   # 65% word overlap → treat as duplicate
                return True
        return False

    def show(self):
        """Print current profile to terminal."""
        lines = self._load()
        if not lines:
            print(c("yellow", "  [improve] Profile file is empty or missing."))
            return
        print()
        _top, _bw = _box_top(f"USER PROFILE ({IMPROVE_PROFILE_PATH})", "cyan")
        print(_top)
        for i, ln in enumerate(lines, 1):
            # First line of entry includes the number prefix
            prefix = f"  {i:>2}. "
            indent = " " * len(prefix)
            first, *rest = (ln or "(empty)").splitlines() or ["(empty)"]
            for row in _box_safe(prefix + first, _bw, "║"):
                print(row)
            for cont in rest:
                for row in _box_safe(indent + cont, _bw, "║"):
                    print(row)
        print(_box_bot(_bw, "cyan"))
        print()

    def inject_into_prompt(self) -> str:
        """Return a short context block to prepend to AI prompts."""
        lines = self._load()
        if not lines:
            return ""
        facts = "\n".join(f"  - {l}" for l in lines[:20])
        return (
            "══ USER PROFILE (learned preferences) ══\n"
            f"{facts}\n"
            "════════════════════════════════════════\n"
        )

    def preferred_language(self) -> str:
        """Scan profile lines for a language preference and return it.
        Returns empty string if none found."""
        lang_keywords = {
            "bangla": "Bangla", "bengali": "Bangla",
            "hindi": "Hindi", "arabic": "Arabic",
            "french": "French", "spanish": "Spanish",
            "german": "German", "chinese": "Chinese",
            "japanese": "Japanese", "korean": "Korean",
            "russian": "Russian", "portuguese": "Portuguese",
            "turkish": "Turkish", "urdu": "Urdu",
            "persian": "Persian", "farsi": "Persian",
        }
        for line in self._load():
            lower = line.lower()
            if "language" in lower or "communicat" in lower or "prefer" in lower or "talk" in lower or "speak" in lower:
                for kw, label in lang_keywords.items():
                    if kw in lower:
                        return label
            # Also catch bare mentions like "User prefers Bangla."
            for kw, label in lang_keywords.items():
                if kw in lower:
                    return label
        return ""


# ══════════════════════════════════════════════════════════════
# TELEGRAM NOTIFIER
# ══════════════════════════════════════════════════════════════

TELEGRAM_CONFIG_PATH = os.path.join(_REAL_HOME, ".hackers_ai_telegram.json")

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

    def send_with_scope_keyboard(self, text: str, callback_id: str) -> int:
        """Send a scope-confirmation message with ✅ Y / 💾 Add / ❌ N inline buttons.
        Returns message_id.  callback data values: yes | add | no"""
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        if len(clean) > 3800:
            clean = clean[:3800] + "\n...[truncated]"
        markup = {
            "inline_keyboard": [[
                {"text": "✅  Yes (once)",        "callback_data": f"{callback_id}:yes"},
                {"text": "💾  Yes + Add to list", "callback_data": f"{callback_id}:add"},
                {"text": "❌  No, cancel",         "callback_data": f"{callback_id}:no"},
            ]]
        }
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

    def stop(self, reason: str = ""):
        """Stop the polling thread and notify the user."""
        if self.enabled and self.token and self.user_id:
            # Send before setting stop event so the thread is still alive to deliver it
            msg = "🔴 <b>Hackers AI is going offline</b>"
            if reason:
                msg += f"\n{reason}"
            try:
                self.send(msg)
            except Exception:
                pass
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
                        # data format: "<callback_id>:yes" or "<callback_id>:add" or "<callback_id>:no"
                        if ":" in cq_data:
                            cb_key, choice = cq_data.rsplit(":", 1)
                            if cb_key in self._pending_confirms:
                                entry = self._pending_confirms[cb_key]
                                # store raw choice so callers can distinguish yes/add/no
                                entry["choice"] = choice
                                entry["result"] = (choice in ("yes", "add"))
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
                self._stop_evt.set()  # stop without sending duplicate closing message
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

DB_PATH        = os.path.join(_REAL_HOME, ".hackers_ai.db")
MCP_CONFIG_PATH = os.path.join(_REAL_HOME, ".hackers_ai_mcp.json")
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
                              Author: IMApurbo
"""

COLORS = {
    "reset":   "\033[0m",  "bold":    "\033[1m",   "dim":     "\033[2m",
    "red":     "\033[91m", "green":   "\033[92m",  "yellow":  "\033[93m",
    "blue":    "\033[94m", "magenta": "\033[95m",  "cyan":    "\033[96m",
    "white":   "\033[97m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"

def _rl_wrap(ansi_str: str) -> str:
    """Wrap every ANSI escape sequence in readline non-printing markers \\001...\\002.
    This prevents readline from counting invisible escape bytes as visible characters,
    which otherwise causes cursor miscalculation and terminal corruption when typing
    long input (especially with MCP prefix in the prompt).
    """
    return re.sub(r'(\033\[[0-9;]*m)', r'\001\1\002', ansi_str)

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
            raw = f.read()
        data = json.loads(raw)
        if not isinstance(data.get("mcpServers"), dict):
            data["mcpServers"] = {}
        return data
    except json.JSONDecodeError as e:
        print(c("red", f"  [MCP] Config file has invalid JSON: {e}"))
        print(c("dim", f"  Fix it with: /config  (path: {MCP_CONFIG_PATH})"))
        return {"mcpServers": {}}
    except Exception as e:
        print(c("red", f"  [MCP] Could not read config: {e}"))
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
    """Open MCP config in the user's preferred editor, always via sudo so the
    root-owned config file (~/.hackers_ai_mcp.json) can be written."""
    _mcp_config_ensure()
    # Pick editor: $VISUAL > $EDITOR > nano > vi
    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or shutil.which("nano")
        or shutil.which("vi")
        or "nano"
    )
    try:
        if os.geteuid() == 0:
            # Already root — open directly (no sudo wrapper needed)
            subprocess.run([editor, MCP_CONFIG_PATH])
        else:
            # Not root — use sudo so the editor can write to the root-owned file
            subprocess.run(["sudo", editor, MCP_CONFIG_PATH])
    except Exception as e:
        print(c("red", f"  Could not open editor: {e}"))
        print(c("dim", f"  Edit manually: sudo {editor} {MCP_CONFIG_PATH}"))

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
            # Permanent profile log — never cleared, accumulates across sessions
            # so _improver.update() always has data even after clear_history()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    role      TEXT NOT NULL,
                    content   TEXT NOT NULL,
                    timestamp TEXT DEFAULT (datetime('now'))
                )""")
            conn.commit()

    # ── Conversation ───────────────────────────────────────
    def add_message(self, role: str, content: str, model: str = DEFAULT_MODEL):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, model) VALUES (?,?,?)",
                (role, content, model))
            # Also append to permanent profile_log (never cleared)
            conn.execute(
                "INSERT INTO profile_log (role, content) VALUES (?,?)",
                (role, content[:2000]))
            conn.commit()

    def get_history(self, limit: int = MAX_HISTORY) -> list:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def get_profile_history(self, limit: int = 40) -> list:
        """Return permanent cross-session history for profile learning.
        Never cleared — survives clear_history() calls."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM profile_log ORDER BY id DESC LIMIT ?",
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
            if "projectdiscovery" in out or ("httpx" in out and "next generation" not in out):
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
# SECTION 6.5 — SMART FILE EDITOR
# Surgical patch engine: edits only the lines that need changing
# instead of rewriting the whole file. Used by CodeGen when the
# AI generates file-edit tasks.
# ══════════════════════════════════════════════════════════════

class SmartFileEditor:
    """
    Applies minimal, surgical edits to a file.

    Four operation types (all driven by an LLM-produced JSON plan):
      • replace  — swap old_text for new_text (supports multi-line spans)
      • insert   — insert new_text after the line containing anchor
      • delete   — delete the first line containing target_text
      • rewrite  — full-file rewrite (fallback for large/complex edits)

    Matching is done against the whole file as a single string so
    multi-line old_text patterns work correctly.
    """

    # Max chars sent to LLM in one shot.  Larger files are sent in
    # CHUNK_SIZE windows centred on the lines most likely to change.
    PROMPT_LIMIT = 12000
    CHUNK_SIZE   = 10000

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    # ── Public API ─────────────────────────────────────────────
    def edit(self, filepath: str, instruction: str) -> dict:
        """
        Edit `filepath` according to natural-language `instruction`.
        Returns {"success": bool, "stdout": str, "stderr": str,
                 "ops_applied": int, "returncode": int,
                 "elapsed": float, "cancelled": False, "command": str}
        """
        t0 = time.time()
        tag = f"[SmartEdit] {os.path.basename(filepath)}"

        # ── 1. Read the file ───────────────────────────────────
        if not os.path.isfile(filepath):
            return self._err(f"File not found: {filepath}", tag, t0)
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
            original_lines = original.splitlines(keepends=True)
        except Exception as e:
            return self._err(f"Cannot read file: {e}", tag, t0)

        # ── 2. Ask the LLM for a minimal ops list ──────────────
        ops = self._plan_ops(filepath, original, original_lines, instruction)
        if ops is None:
            # Hard fallback: ask LLM to rewrite the whole file
            print(c("yellow", "  [SmartEdit] Ops plan failed — attempting full rewrite..."))
            ops = self._plan_rewrite(filepath, original, instruction)
            if ops is None:
                return self._err("LLM could not produce a valid edit plan.", tag, t0)

        if not ops:
            return {
                "success": True, "stdout": "No changes needed.",
                "stderr": "", "ops_applied": 0,
                "returncode": 0, "elapsed": round(time.time() - t0, 2),
                "cancelled": False, "command": tag,
            }

        # ── 3. Apply ops ───────────────────────────────────────
        # Work on the whole file as a string for multi-line replace support,
        # but keep a lines list for insert/delete ops.
        content  = original
        lines    = list(original_lines)
        applied, errors = 0, []

        for op in ops:
            kind = op.get("op", "")
            try:
                if kind == "replace":
                    content, ok = self._op_replace_str(content, op["old_text"], op["new_text"])
                    if ok:
                        lines = content.splitlines(keepends=True)
                    else:
                        # Fallback: try normalised whitespace match
                        content, ok = self._op_replace_fuzzy(content, op["old_text"], op["new_text"])
                        if ok:
                            lines = content.splitlines(keepends=True)
                elif kind == "insert":
                    lines, ok = self._op_insert(lines, op["anchor"], op["new_text"])
                    if ok:
                        content = "".join(lines)
                elif kind == "delete":
                    lines, ok = self._op_delete(lines, op["target_text"])
                    if ok:
                        content = "".join(lines)
                elif kind == "rewrite":
                    # Full-file rewrite op — LLM returns the complete new content
                    content = op["new_content"]
                    lines   = content.splitlines(keepends=True)
                    ok = True
                else:
                    errors.append(f"Unknown op: {kind}")
                    continue
                if ok:
                    applied += 1
                else:
                    errors.append(f"Op '{kind}' — pattern not found in file.")
            except KeyError as e:
                errors.append(f"Op '{kind}' missing field {e}.")

        # ── 4. Write back only if something changed ────────────
        if applied == 0:
            # Last-resort: ask LLM to do a full rewrite
            print(c("yellow", "  [SmartEdit] All ops failed — falling back to full rewrite..."))
            rw_ops = self._plan_rewrite(filepath, original, instruction)
            if rw_ops:
                for op in rw_ops:
                    if op.get("op") == "rewrite":
                        content = op["new_content"]
                        applied += 1
                        break
            if applied == 0:
                msg = "No ops matched. " + "; ".join(errors)
                return self._err(msg, tag, t0)

        if content == original:
            return {
                "success": True, "stdout": "No changes needed (content identical).",
                "stderr": "", "ops_applied": 0,
                "returncode": 0, "elapsed": round(time.time() - t0, 2),
                "cancelled": False, "command": tag,
            }

        try:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            return self._err(f"Cannot write file: {e}", tag, t0)

        summary = f"✓ Applied {applied}/{len(ops)} ops to {filepath}"
        if errors:
            summary += "\n  Warnings: " + "; ".join(errors)
        print(c("green", f"  [SmartEdit] {summary}"))
        return {
            "success": True, "stdout": summary,
            "stderr": "; ".join(errors) if errors else "",
            "ops_applied": applied,
            "returncode": 0, "elapsed": round(time.time() - t0, 2),
            "cancelled": False, "command": tag,
        }

    # ── LLM planners ───────────────────────────────────────────
    def _plan_ops(self, filepath: str, content: str, lines: list, instruction: str):
        """Ask the LLM for a minimal surgical ops list.
        For large files, send a focused window around the most relevant lines."""
        numbered = "".join(f"{i+1}: {l}" for i, l in enumerate(lines))

        if len(numbered) > self.PROMPT_LIMIT:
            # Find the window of lines most likely mentioned in the instruction
            # by scoring each line for keyword overlap with the instruction.
            keywords = set(re.findall(r"\b\w{4,}\b", instruction.lower()))
            scores   = []
            for i, line in enumerate(lines):
                sc = sum(1 for kw in keywords if kw in line.lower())
                scores.append((sc, i))
            scores.sort(reverse=True)
            # Grab the top-scoring centre line and expand a window around it
            centre = scores[0][1] if scores else len(lines) // 2
            half   = self.CHUNK_SIZE // 2
            start  = max(0, centre - half // (len(lines[0]) or 1))
            end    = min(len(lines), start + self.CHUNK_SIZE // (len(lines[0]) or 1))
            window = lines[start:end]
            numbered = "".join(f"{start+i+1}: {l}" for i, l in enumerate(window))
            if len(numbered) > self.PROMPT_LIMIT:
                numbered = numbered[:self.PROMPT_LIMIT] + "\n... (truncated) ..."
            trunc_note = (
                f"NOTE: File has {len(lines)} lines. Showing lines {start+1}–{end} "
                f"(most relevant window). Use line numbers from this view for anchors.\n\n"
            )
        else:
            trunc_note = ""

        prompt = (
            "You are a surgical file-patch engine.\n"
            "Given a file's contents and an edit instruction, produce the MINIMAL list of\n"
            "operations needed. Do NOT rewrite the whole file — only touch changed lines.\n\n"
            "OPERATION TYPES (a JSON array of these objects):\n"
            '  {"op":"replace","old_text":"<exact multi-or-single line text>","new_text":"<replacement>"}\n'
            '  {"op":"insert","anchor":"<exact substring of the line to insert AFTER>","new_text":"<new line(s)>"}\n'
            '  {"op":"delete","target_text":"<exact substring of the line to delete>"}\n\n'
            "CRITICAL RULES:\n"
            "  1. old_text / anchor / target_text must be EXACT copy-paste from the file.\n"
            "     Copy the text character-for-character including indentation and punctuation.\n"
            "  2. For multi-line replacements, old_text must span the EXACT lines as shown\n"
            "     including all newlines (\\n) between them.\n"
            "  3. new_text must end with \\n unless it is the last line of the file.\n"
            "  4. Prefer replace over delete+insert for changing existing code.\n"
            "  5. Return [] if no changes are needed.\n"
            "  6. MUST wrap output in ```json fenced block — no other text.\n"
            "  7. Escape ALL double-quotes inside JSON string values as \\\"\n"
            "  8. Escape ALL literal backslashes as \\\\.\n\n"
            f"FILE: {filepath}\n"
            f"INSTRUCTION: {instruction}\n\n"
            f"{trunc_note}"
            f"FILE CONTENTS (line-numbered):\n{numbered}\n\n"
            "Respond with ONLY a ```json fenced block:"
        )
        return self._ask_and_parse(prompt)

    def _plan_rewrite(self, filepath: str, content: str, instruction: str):
        """Full-file rewrite fallback — returns a single rewrite op."""
        # For very large files cap what we send
        send_content = content if len(content) <= 20000 else content[:20000] + "\n... (truncated)"
        prompt = (
            "You are a file editor. Rewrite the ENTIRE file applying the instruction below.\n"
            "Return ONLY a ```json fenced block with a single rewrite op:\n"
            '  [{"op":"rewrite","new_content":"<complete new file content>"}]\n\n'
            "RULES:\n"
            "  1. new_content must be the COMPLETE new file — do not truncate.\n"
            "  2. Escape ALL double-quotes as \\\" and backslashes as \\\\.\n"
            "  3. No explanation outside the ```json block.\n\n"
            f"FILE: {filepath}\n"
            f"INSTRUCTION: {instruction}\n\n"
            f"CURRENT FILE:\n{send_content}\n\n"
            "Respond with ONLY a ```json fenced block:"
        )
        return self._ask_and_parse(prompt)

    def _ask_and_parse(self, prompt: str):
        """Send prompt to LLM and robustly extract a JSON array."""
        try:
            agent = FreeLLM(model=self.model)
            raw   = agent.ask(prompt).strip()
            # Prefer fenced ```json block
            fenced = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", raw)
            if fenced:
                raw = fenced.group(1).strip()
            else:
                raw = re.sub(r"^```[a-z]*\n?", "", raw).strip()
                raw = re.sub(r"\n?```$", "", raw).strip()
            # Extract the JSON array even if surrounded by stray text
            arr_match = re.search(r"(\[[\s\S]*\])", raw)
            if arr_match:
                raw = arr_match.group(1)
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception as e:
            print(c("yellow", f"  [SmartEdit] Plan parse error: {e}"))
        return None

    # ── Op implementations ──────────────────────────────────────
    @staticmethod
    def _op_replace_str(content: str, old_text: str, new_text: str):
        """Exact string replacement across the whole file content."""
        if old_text in content:
            return content.replace(old_text, new_text, 1), True
        return content, False

    @staticmethod
    def _op_replace_fuzzy(content: str, old_text: str, new_text: str):
        """Whitespace-normalised fallback: collapse runs of spaces/newlines before
        comparing, so minor LLM indentation mistakes still match."""
        def _norm(s):
            return re.sub(r"[ \t]+", " ", re.sub(r"\r\n|\r", "\n", s)).strip()
        norm_content = _norm(content)
        norm_old     = _norm(old_text)
        if norm_old and norm_old in norm_content:
            # Find the real start position in the original content
            # by locating the first occurrence of the first non-blank word
            first_word = re.search(r"\S+", old_text)
            if first_word:
                idx = content.find(first_word.group())
                if idx != -1:
                    # Find end by character count approximation
                    end = idx + len(old_text)
                    # Try to snap end to a line boundary
                    nl = content.find("\n", end - 5)
                    if nl != -1 and nl < end + 50:
                        end = nl + 1
                    return content[:idx] + new_text + content[end:], True
        return content, False

    @staticmethod
    def _op_insert(lines: list, anchor: str, new_text: str):
        for i, line in enumerate(lines):
            if anchor in line:
                new_lines = []
                for nl in new_text.splitlines():
                    new_lines.append(nl + "\n")
                if not new_lines:
                    new_lines = [new_text if new_text.endswith("\n") else new_text + "\n"]
                lines[i+1:i+1] = new_lines
                return lines, True
        return lines, False

    @staticmethod
    def _op_delete(lines: list, target_text: str):
        for i, line in enumerate(lines):
            if target_text in line:
                lines.pop(i)
                return lines, True
        return lines, False

    @staticmethod
    def _err(msg: str, tag: str, t0: float) -> dict:
        print(c("red", f"  [SmartEdit] ✗ {msg}"))
        return {
            "success": False, "stdout": "", "stderr": msg,
            "ops_applied": 0, "returncode": -1,
            "elapsed": round(time.time() - t0, 2),
            "cancelled": False, "command": tag,
        }


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

        # ── Smart-edit fast path ───────────────────────────────
        # If the task is about editing an existing file, delegate to
        # SmartFileEditor so only the changed lines are touched.
        _edit_kw = re.compile(
            r'\b(edit|modify|change|update|fix|patch|replace|rename|'
            r'add line|remove line|delete line|insert|append to|'
            r'comment out|uncomment|refactor)\b',
            re.IGNORECASE
        )
        _file_re = re.compile(r'[\w/\.\-][\w/\.\-]{2,}\.\w{1,6}')
        _combined = f"{task} {user_input}"
        if _edit_kw.search(_combined):
            _fmatch = _file_re.search(_combined)
            if _fmatch:
                _fpath = _fmatch.group(0)
                # Resolve relative paths against cwd
                if not os.path.isabs(_fpath):
                    _fpath = os.path.join(cwd, _fpath)
                if os.path.isfile(_fpath):
                    print(c("cyan", f"  [CodeGen] → SmartEdit mode for: {_fpath}"))
                    editor = SmartFileEditor(model=model)
                    return editor.edit(_fpath, _combined)

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
            "  11. NEVER rewrite an entire existing file just to change a few lines.\n"
            "      Instead, read the file, change only the necessary parts, write back.\n"
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
        self._lock   = threading.RLock()
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
                        rr, _, _ = select.select([self._proc.stderr], [], [], 1.0)
                        if rr:
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
        """Check if the server process is alive without sending any protocol messages."""
        try:
            self._start()
            return self._proc is not None and self._proc.poll() is None
        except Exception:
            return False

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
  - ANY task when an MCP server is active — the agent will use MCP tools
    to discover the needed context (e.g. list files, read config, query resources)
    rather than bother the user with a question

MCP CONTEXT RULE (CRITICAL — highest priority):
  When an MCP server is connected, ALWAYS set ready=true, regardless of
  missing context. The agent will use the MCP server's tools to discover
  what it needs (directory listings, resource reads, environment queries, etc.).
  NEVER set ready=false when MCP is active — instead build the best possible
  enriched_task and let the planner use MCP tools to fill any gaps.

ready=false ONLY when:
  - NO MCP server is active
  - task obviously needs a remote target AND none exists anywhere in history
  - user has already been asked once and still hasn't provided the info

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT — respond ONLY with:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```json
{
  "intent": "task",
  "ready": true,
  "found_in": "task|history|local|mcp|fallback",
  "enriched_task": "<complete task string with all values filled in>",
  "question": null
}
```
OR when more info is genuinely needed (NO MCP active only):
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

    # Fast-path regex: pure casual chat — ALWAYS informational, skip LLM call
    # These are short greetings, name-exchanges, compliments, small talk
    _CASUAL_RE = re.compile(
        r'^(hello|hi|hey|hiya|sup|salut|yo|howdy|'
        r'how are you|kemon acho|kemon achen|ki khobor|ki obostha|'
        r'tumi ke|ami ke|tomar nam ki|tomar nam|ami.*nam|amar nam|'
        r'tumi.*bolo|bolo amar|apnar nam|tumi.*bujhcho|'
        r'thik ache|okay|ok|thanks|thank you|dhonnobad|shukriya|'
        r'good|nice|cool|great|awesome|wow|lol|haha|'
        r'ami.*apurbo|ami apurbo|amar nam.*apurbo|apurbo.*ami|'
        r'tomar nam.*ima|tumi.*ima|ima.*tumi|'
        r'bye|goodbye|see you|abar dekha hobe|'
        r"what'?s up|what is up|hows it going)"
        r'[\s!?.]*$',
        re.IGNORECASE
    )

    def __init__(self, model: str = DEFAULT_MODEL, mcp_active: bool = False):
        self.model           = model
        self._question_count = 0
        # When MCP is active, never ask the user — use tools to discover context instead
        self._max_questions  = 0 if mcp_active else 2
        self._mcp_active     = mcp_active

    def analyze(self, user_input: str, history: list) -> dict:
        """
        Returns dict with keys: intent, ready, found_in, enriched_task, question
        """
        _fallback_task = {
            "intent": "task", "ready": True, "found_in": "fallback",
            "enriched_task": user_input, "question": None,
        }

        # ── Fast path: casual chat — ALWAYS informational, no LLM call needed ─
        if self._CASUAL_RE.match(user_input.strip()):
            return {
                "intent": "informational", "ready": True, "found_in": "task",
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
                # When MCP is active: never ask the user — always proceed and let
                # the planner use MCP tools to discover missing context.
                if self._mcp_active:
                    result["ready"] = True
                    result["question"] = None
                    if not result.get("enriched_task"):
                        result["enriched_task"] = user_input
                    if result.get("found_in") in (None, "none"):
                        result["found_in"] = "mcp"
                else:
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
                  profile: dict, model: str = DEFAULT_MODEL,
                  _mcp_client=None) -> Optional[dict]:
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
            "If context is missing, add a discovery step using an MCP tool — never ask the user. "
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
                        # In MCP mode: prefer MCP tools, but allow fallback to
                        # native command/python when no MCP tool covers the task.
                        if mcp_active and stype not in ("mcp_call", "info", "smart_edit"):
                            exec_tool = profile.get("active_mcp_exec_tool", "")
                            if stype == "command" and exec_tool:
                                # Rewrite as mcp_call -> execute_command (MCP shell tool)
                                step["type"] = "mcp_call"
                                step["args"] = {"command": step.get("command", "")}
                                step["tool"] = exec_tool
                                step["command"] = ""
                                print(c("dim", f"  [Planner] MCP mode — rewrote command→{exec_tool}: {desc_lower[:50]}"))
                            elif stype == "command":
                                # No exec_tool available — run as native shell command
                                print(c("dim", f"  [Planner] MCP mode — no exec_tool, native shell: {desc_lower[:50]}"))
                            elif stype == "python":
                                # Python steps always run natively (MCP has no Python runner)
                                print(c("dim", f"  [Planner] MCP mode — python step runs natively: {desc_lower[:50]}"))
                            else:
                                print(c("dim", f"  [Planner] MCP mode — dropped unknown type '{stype}': {desc_lower[:50]}"))
                                continue
                        # ── Plan-time schema strip for mcp_call steps ───────────
                        if stype == "mcp_call":
                            if _mcp_client:
                                _tn = step.get("tool", "")
                                _ta = step.get("args") or {}
                                _ts = {}
                                for _tt in (_mcp_client.list_tools() or []):
                                    if _tt.get("name") == _tn:
                                        _ts = _tt.get("inputSchema") or {}
                                        break
                                _tp = _ts.get("properties", {}) if isinstance(_ts, dict) else {}
                                if _tp:
                                    _vk  = set(_tp.keys())
                                    _bk  = set(_ta.keys()) - _vk
                                    if _bk:
                                        print(c("yellow",
                                            f"  [Planner] ✂ stripped hallucinated args for "
                                            f"{_tn}: {sorted(_bk)}"))
                                        step["args"] = {k: v for k, v in _ta.items() if k in _vk}
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
        # ── User profile facts (from --improve / learned preferences) ──
        user_profile_block = profile.get("user_profile_facts", "")

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
  6. NEVER ask the user for more information. If context is missing (target IP, filename,
     path, config value, etc.), add a discovery step FIRST — use an MCP tool to find it:
       • list files/directories → use the appropriate list/read tool
       • unknown target → use a tool to enumerate hosts or read a config
       • unknown parameter → use a tool to inspect the environment or resources
     Chain that discovery step (depends_on=[]) ahead of the steps that need its output.
  7. Prefer acting and discovering over asking. The user wants results, not questions.
  8. CRITICAL — ARGS MUST MATCH SCHEMA EXACTLY:
     • Use ONLY the parameter names shown in the TOOL CATALOGUE above.
     • NEVER invent parameters not listed (e.g. never add "offset", "count",
       "limit", "page", "pagination" unless they explicitly appear in the tool's ARGS).
     • For REQUIRED params: you MUST supply a value — use the EXAMPLE as a template.
     • For OPTIONAL params: omit them unless you have a specific value to pass.
     • If a tool has no args (ARGS: none), pass an empty args dict: "args": {{}}
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

{user_profile_block}
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
13. FILE EDITING — CRITICAL: When the task is to edit/modify/change/patch/add
    to/remove from an EXISTING file, you MUST use type="smart_edit".
    NEVER use type="command" or type="python" to edit an existing file.
    smart_edit applies only the minimal necessary changes without rewriting.
    Format: {{"id": N, "type": "smart_edit", "command": "<absolute_file_path>",
              "description": "<precise natural-language description of ALL changes needed>",
              "depends_on": []}}
    BAD  : {{"type":"command","command":"snake_game_fixed.html"}}  ← NEVER do this
    BAD  : {{"type":"python","command":"","description":"edit the html file"}}
    GOOD : {{"type":"smart_edit","command":"/home/kali/Desktop/snake_game_fixed.html",
              "description":"add speedMultiplier variable and boost logic"}}
    Use type="python" ONLY when creating a NEW file from scratch or doing non-edit tasks.
12. NEVER use 'cd' in shell commands — cd is a shell built-in and CANNOT be
    run as a standalone command or pre-flight step. Instead, ALWAYS use full
    absolute paths directly in every command. For example:
      WRONG : cd /home/user/Desktop && mv *.txt /tmp/
      RIGHT : mv /home/user/Desktop/*.txt /tmp/
    If multiple commands must operate in the same directory, embed the path
    in each command individually rather than chaining with cd.

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

type values: "command" | "python" | "smart_edit" | "mcp_call" | "info"
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
                "PRIORITY RULES for MCP mode:\n"
                "1. ALWAYS prefer type=mcp_call for any action covered by the MCP TOOL CATALOGUE.\n"
                "2. If a task cannot be done with any MCP tool, use type=command (shell) or type=python.\n"
                "3. python steps: type=python, command=empty string, description=what to do.\n"
                "4. command steps: type=command, command=<shell command>.\n"
                "5. For mcp_call: put arg values in args dict — match EXACT parameter names from catalogue.\n"
                "6. If context is missing (target, path, ID), add a discovery step (MCP tool preferred) first.\n"
                "7. Do NOT ask the user for information — act and discover.\n"
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
        # Pre-build a schema lookup dict for plan-time arg stripping
        _plan_schemas: dict = {}
        _plan_mcp_client = getattr(self, "_mcp_client", None)
        if _plan_mcp_client:
            try:
                for _pt in (_plan_mcp_client.list_tools() or []):
                    _pn = _pt.get("name", "")
                    _ps = _pt.get("inputSchema") or {}
                    _pp = _ps.get("properties", {}) if isinstance(_ps, dict) else {}
                    if _pn and _pp:
                        _plan_schemas[_pn] = set(_pp.keys())
            except Exception:
                pass

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
                        # In MCP mode: prefer MCP tools, but allow fallback to
                        # native command/python when no MCP tool covers the task.
                        if mcp_active and stype not in ("mcp_call", "info", "smart_edit"):
                            exec_tool = profile.get("active_mcp_exec_tool", "")
                            if stype == "command" and exec_tool:
                                # Rewrite as mcp_call -> execute_command (MCP shell tool)
                                step["type"] = "mcp_call"
                                step["args"] = {"command": step.get("command", "")}
                                step["tool"] = exec_tool
                                step["command"] = ""
                                print(c("dim", f"  [Planner] MCP mode — rewrote command→{exec_tool}: {desc_lower[:50]}"))
                            elif stype == "command":
                                # No exec_tool available — run as native shell command
                                print(c("dim", f"  [Planner] MCP mode — no exec_tool, native shell: {desc_lower[:50]}"))
                            elif stype == "python":
                                # Python steps always run natively (MCP has no Python runner)
                                print(c("dim", f"  [Planner] MCP mode — python step runs natively: {desc_lower[:50]}"))
                            else:
                                print(c("dim", f"  [Planner] MCP mode — dropped unknown type '{stype}': {desc_lower[:50]}"))
                                continue
                        # ── Plan-time schema strip for mcp_call steps ───────────
                        # Remove any args the LLM hallucinated that don't exist in
                        # the real tool schema (e.g. offset, count, limit, page …)
                        if stype == "mcp_call" and _plan_schemas:
                            t_name = step.get("tool", "")
                            t_args = step.get("args") or {}
                            if t_name in _plan_schemas:
                                valid_keys = _plan_schemas[t_name]
                                bad_keys   = set(t_args.keys()) - valid_keys
                                if bad_keys:
                                    print(c("yellow",
                                        f"  [Planner] ✂ stripped hallucinated args for "
                                        f"{t_name}: {sorted(bad_keys)}"))
                                    step["args"] = {k: v for k, v in t_args.items()
                                                    if k in valid_keys}
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
        user_profile_block = profile.get("user_profile_facts", "")

        # Dynamically extract known user name and AI nickname from profile facts
        _user_name   = None   # unknown until user tells us
        _ai_nickname = None   # unknown until user gives one
        for line in (user_profile_block or "").splitlines():
            ll = line.lower()
            if _user_name is None and ("user's name is" in ll or "user name is" in ll):
                m = re.search(r"user'?s? name is (\w+)", ll)
                if m:
                    _user_name = m.group(1).capitalize()
            if _ai_nickname is None and "calls the ai" in ll:
                m = re.search(r"calls the ai (\w+)", ll)
                if m:
                    _ai_nickname = m.group(1).capitalize()

        # Build identity block only with what is actually known
        identity_lines = [
            "━━━ IDENTITY ━━━",
            "You are the AI assistant in this conversation.",
        ]
        if _ai_nickname:
            identity_lines.append(f"The user has given you the nickname '{_ai_nickname}' — use it as your own name.")
        else:
            identity_lines.append("If the user gives you a nickname, accept it immediately and use it as your own name.")
        if _user_name:
            identity_lines.append(f"The user's name is {_user_name} — address them by name naturally.")
        else:
            identity_lines.append("If the user tells you their name, use it naturally going forward.")
        identity_lines += [
            "NEVER call the user by your own nickname.",
            "NEVER call yourself by the user's name.",
            "Keep these roles clear in every reply.",
        ]
        identity_block = "\n".join(identity_lines)

        # Build conversation prefix labels from known names
        user_label = _user_name or "USER"
        ai_label   = _ai_nickname or "AI"

        system_ctx = textwrap.dedent(f"""
⚠ ABSOLUTE OUTPUT RULE — READ FIRST, NEVER BREAK:
You MUST write EVERY word using ONLY plain English/Latin letters (a-z A-Z).
ZERO exceptions. No Bengali script. No Arabic. No Chinese. No Devanagari. No Cyrillic.
No Unicode language characters of ANY kind.
If the user speaks Bangla → write Bangla phonetically: "kemon acho", "ami valo achi".
If the user speaks Hindi → "kya haal hai", "theek hai".
If ANY non-Latin character appears in your output it is a CRITICAL FAILURE.

{identity_block}

━━━ PERSONALITY & CONVERSATION RULES ━━━
You are Hackers AI — a skilled, friendly Linux/hacking assistant with a real personality.
Developed by AKM Korishee Apurbo (@IMApurbo).

{user_profile_block}
SYSTEM:
  OS: {profile.get('distro','Linux')} | Kernel: {profile.get('kernel','')}
  Host: {profile.get('hostname','')} | IP: {profile.get('ip','')} | Root: True
  CWD: {profile.get('cwd','/root')}
  Target: {profile.get('sticky_target','(none)')}
  Tools: {tools_str}

BEHAVIOUR:
- Warm, casual, human personality — not a cold robot
- Accept any nickname the user gives YOU (the AI) and use it as your own name going forward
- For casual chat / greetings / small talk: JUST CHAT BACK. Do NOT mention commands, scanning,
  reverse engineering, or any technical work unless the user brings it up first.
- NEVER redirect casual conversation toward hacking topics or commands unprompted
- NEVER suggest commands or ask "what do you need?" after casual messages
- Only mention hacking/commands when the user EXPLICITLY asks for technical help
- Be SHORT — no long paragraphs
- No markdown: no ``` fences, no ** bold, no # headers
- Commands on plain text lines

REMINDER — OUTPUT IN LATIN LETTERS ONLY. NOT A SINGLE NON-LATIN CHARACTER.
        """).strip()
        parts = [system_ctx, "\n--- RECENT CONVERSATION ---"]
        for h in history[-4:]:
            prefix  = user_label if h["role"] == "user" else ai_label
            content = h["content"][:300].replace("\n", " ")
            parts.append(f"{prefix}: {content}")
        parts.append(f"\n{user_label}: {user_input}\n{ai_label}:")
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
                self._lprint(c("dim", "  " + "─"*(_tw()-2)))

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
                    # Each thread passes args through _run_with_healing via the step
                    # directly — avoid writing to shared self._current_step_args to
                    # prevent race conditions between parallel workers.
                    with lk:
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
        return _REAL_HOME

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
                        "'query' is a required" in txt.lower() or
                        "is a required property" in txt.lower() or
                        ("missing" in txt.lower() and "input_value" in txt.lower()))

            # ── helper: strip args to only schema-valid keys ────────
            # This is the PRIMARY fix: before calling any tool, drop every key
            # that is not declared in the tool's inputSchema.  The LLM planner
            # sometimes stuffs planning metadata (intent, summary, requires_root …)
            # into the args dict instead of real tool parameters.
            def _strip_to_schema(t_name, raw_args):
                schema = _tool_schema(t_name)
                props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                if not props:
                    # No schema info available — pass args through unchanged
                    return raw_args
                valid_keys = set(props.keys())
                stripped   = {k: v for k, v in raw_args.items() if k in valid_keys}
                dropped    = set(raw_args.keys()) - valid_keys
                if dropped:
                    _lp(c("yellow", f"  [MCP] ⚠ dropped unknown arg keys for {t_name}: {sorted(dropped)}"))
                return stripped

            # ── helper: fix args via LLM using real schema ─────────
            def _fix_args(t_name, bad_args, err_txt):
                schema = _tool_schema(t_name)
                props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                req    = schema.get("required", []) if isinstance(schema, dict) else []
                if not props:
                    return None  # Nothing to fix without a schema
                # Strip planning-context keys from bad_args before showing the LLM
                valid_keys = set(props.keys())
                clean_bad  = {k: v for k, v in bad_args.items() if k in valid_keys}

                # Build param hint: REQUIRED/OPTIONAL  name(type): description
                # Also build example values for required params
                hints = []
                example_args = {}
                for pn, pi in (props.items() if isinstance(props, dict) else []):
                    label = "REQUIRED" if pn in req else "OPTIONAL"
                    if isinstance(pi, dict):
                        pt = pi.get("type", "any")
                        if pi.get("enum"):
                            pt = "|".join(str(e) for e in pi["enum"][:6])
                        pd = pi.get("description", "") if isinstance(pi, dict) else ""
                        # Build a sensible example value
                        if pi.get("enum"):
                            example_args[pn] = pi["enum"][0]
                        elif pt == "integer":
                            example_args[pn] = 0
                        elif pt == "boolean":
                            example_args[pn] = False
                        elif pt == "array":
                            example_args[pn] = []
                        else:
                            example_args[pn] = f"<{pn}>"
                    else:
                        pt = "any"
                        pd = ""
                        example_args[pn] = f"<{pn}>"
                    hints.append(f"  {label:<8} {pn} ({pt}): {pd[:70]}")

                schema_hint = "\n".join(hints) if hints else "(no schema available)"
                req_example = {k: v for k, v in example_args.items() if k in req}

                # Tell the LLM what valid values already exist so it can carry them over
                prompt = (
                    f"An MCP tool call failed because the args dict had wrong or missing keys.\n"
                    f"Tool        : {t_name}\n"
                    f"Valid args from prior attempt: {json.dumps(clean_bad)}\n"
                    f"Error       : {err_txt[:400]}\n\n"
                    f"EXACT PARAMETER SCHEMA for {t_name}:\n{schema_hint}\n\n"
                    f"EXAMPLE of correct required args: {json.dumps(req_example)}\n\n"
                    f"RULES:\n"
                    f"1. Return ONLY a JSON object — nothing else, no explanation.\n"
                    f"2. Include ALL REQUIRED params using the EXACT param names above.\n"
                    f"3. Infer sensible string values for any missing required params based "
                    f"   on the tool name ('{t_name}') and context from valid args.\n"
                    f"4. Do NOT include OPTIONAL params unless you have a specific value.\n"
                    f"5. Do NOT include any params not listed in the schema above.\n"
                    f"6. If a required param is a search term or keyword, infer it from "
                    f"   the tool name (e.g. get_android_manifest → no args needed, "
                    f"   search_classes_by_keyword → search_term might be 'Activity').\n"
                )
                try:
                    agent   = FreeLLM(model=self.model)
                    raw_fix = agent.ask(prompt).strip()
                    fixed   = extract_json(raw_fix)
                    if isinstance(fixed, dict):
                        # Always restrict the result to valid schema keys
                        fixed = {k: v for k, v in fixed.items() if k in valid_keys}
                        missing_req = [r for r in req if r not in fixed]
                        if missing_req:
                            _lp(c("yellow", f"  [MCP] ⚠ LLM fix missing required keys: {missing_req}"))
                            # Last resort: fill missing required string params with empty string
                            # so the call at least goes through and returns a real error
                            for mk in missing_req:
                                pi = props.get(mk, {})
                                pt = pi.get("type", "string") if isinstance(pi, dict) else "string"
                                if pt == "integer":
                                    fixed[mk] = 0
                                elif pt == "boolean":
                                    fixed[mk] = False
                                elif pt == "array":
                                    fixed[mk] = []
                                else:
                                    fixed[mk] = ""
                        return fixed if fixed else None
                except Exception:
                    pass
                return None

            # ── pre-validate: strip args to schema before first call ─
            mcp_args = _strip_to_schema(mcp_tool, mcp_args)

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
                # Retry whenever _fix_args returns a non-None dict — even if the
                # keys look the same as mcp_args, the values may have been corrected.
                if fixed_args is not None and isinstance(fixed_args, dict):
                    _lp(c("dim",    f"  [MCP] corrected args: {json.dumps(fixed_args)[:120]}"))
                    try:
                        raw_result, text_out, elapsed, is_err = _mcp_attempt(mcp_tool, fixed_args)
                        mcp_args = fixed_args
                    except Exception as e2:
                        elapsed  = round(time.time() - start, 2)
                        text_out = str(e2)
                        is_err   = True
                else:
                    _lp(c("red", "  [MCP] could not determine corrected args — check tool schema"))

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

        if stype not in ("python", "smart_edit") and self._is_install_cmd(command):
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

        if stype not in ("python", "smart_edit") and command.strip():
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
                # Shell built-ins — never installable, skip pre-flight entirely
                "cd", "source", "export", "alias", "unalias", "set", "unset",
                "exit", "exec", "eval", "read", "printf", "type", "help",
                "history", "jobs", "fg", "bg", "wait", "trap", "return",
                "break", "continue", "shift", "getopts", "umask", "ulimit",
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

        # ── smart_edit — surgical file patching ───────────────────
        if stype == "smart_edit":
            filepath = command.strip()
            if not os.path.isabs(filepath) and effective_cwd:
                filepath = os.path.join(effective_cwd, filepath)
            instruction = desc or getattr(self, "_user_input", "") or command
            _lp(c("cyan", f"\n  [SmartEdit] ✎ Patching: {filepath}"))
            editor = SmartFileEditor(model=self.model)
            result = editor.edit(filepath, instruction)
            self._log(task_id, step_id, tool, f"smart_edit:{filepath}",
                      result["stdout"], "success" if result["success"] else "error")
            return result

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
                    original_task, completed, profile, self.model,
                    _mcp_client=getattr(self.engine, "_mcp_client", None)
                )
                if not next_plan or not next_plan.get("steps"):
                    print(c("green", "  ✓ Task complete."))
                    break
                steps = next_plan["steps"]
            else:
                print(c("dim", "\n  [→] Generating dependent steps with real output..."))
                next_plan = self.planner.plan_next(
                    original_task, completed, profile, self.model,
                    _mcp_client=getattr(self.engine, "_mcp_client", None)
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

    def summarize(self, raw_results: str, original_request: str, history: list,
                  profile: dict = None) -> str:
        profile = profile or {}
        lang_rule = (
            "Write the summary in the same language the user used for their request. "
            "CRITICAL: ALWAYS use only English/Latin letters — never native scripts "
            "(no Bengali/Arabic/Chinese/Devanagari/Cyrillic or any non-Latin characters). "
            "Write all languages phonetically. "
            "Example: Bangla request → 'scan shesh, port 22 open ache' not Bengali script."
        )
        system_ctx = textwrap.dedent(f"""
⚠ ABSOLUTE OUTPUT RULE: Use ONLY plain English/Latin letters (a-z A-Z 0-9).
Zero non-Latin Unicode characters. No Bengali/Arabic/Chinese/Devanagari script.
Write any language phonetically with English letters only.

You are Hackers AI. Write a SHORT, accurate summary of what just happened.

Rules:
- Report ONLY what actually happened for THIS task
- No generic templates, no filler bullets
- XSS/web test → state which payloads reflected, which were blocked, any confirmed vulns
- Scan → show actual findings (open ports, services, vulnerabilities found)
- Install → state what was installed and result
- Simple command → result in 1-2 sentences
- PLAIN TEXT ONLY — no markdown, no ``` fences, no ** bold, no # headers
- Write in the same language the user used, but ONLY with Latin letters
- REMINDER: zero non-Latin characters in output

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

        _rtop, _rbw = _box_top("RECON PIPELINE", "green")
        print("\n" + _rtop)
        print(_box_row(f"Target : {domain}", "║", _rbw))
        print(_box_row(f"Output : {outdir}", "║", _rbw))
        print(_box_bot(_rbw, "green"))

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
            _atop, _abw = _box_top("AI ASSESSMENT", "red")
            print(_atop)
            for line in ai_summary.splitlines():
                sc = "red" if "critical" in line.lower() else \
                     "yellow" if "high" in line.lower() else "white"
                print(_box_row(c(sc, line), "║", _abw))
            print(_box_bot(_abw, "red"))
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

# ── Smart completer: slash commands + annotated path completions ──
if _PT_OK:
    class _HackersCompleter(_PTCompleter):
        """
        Provides two completion modes:
        • Slash commands  — triggered when text starts with '/'
          Shows: /command  <description>
        • Path completion — triggered when any token contains './', '../', '~/', or '/'
          Shows: name  [dir] or [file] annotation on the right
        """
        _SLASH = {
            "/help":    "Show commands",
            "/clear":   "Clear history",
            "/history": "Last messages",
            "/profile": "System profile / memory / edit",
            "/tools":   "List pentest tools",
            "/sysinfo": "Live system info",
            "/switch":  "Switch model",
            "/target":  "Set sticky target",
            "/auth":    "Authorize target",
            "/shell":   "Drop to bash shell",
            "/recon":   "Full recon pipeline",
            "/note":    "Save target note",
            "/notes":   "Show notes",
            "/delnotes":"Delete notes",
            "/save":    "Save session report",
            "/dryrun":  "Toggle dry-run mode",
            "/config":  "Edit MCP config",
            "/telegram":"Configure Telegram remote",
            "/mcp":     "MCP server control",
            "/improve": "Update user profile from session",
            "/exit":    "Save + exit",
        }

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            word = document.get_word_before_cursor(WORD=True)

            # ── Slash command completion ────────────────────────
            if text.lstrip().startswith("/"):
                typed = text.lstrip()
                for cmd, desc in self._SLASH.items():
                    if cmd.startswith(typed):
                        yield _PTCompletion(
                            cmd,
                            start_position=-len(typed),
                            display=cmd,
                            display_meta=desc,
                        )
                return

            # ── Path completion ─────────────────────────────────
            _PATH_TRIGGERS = ("./", "../", "/", "~/", "~")
            is_path = any(
                word.startswith(t) for t in _PATH_TRIGGERS
            ) or ("/" in word and not word.startswith("-"))

            if not is_path:
                return

            # Expand to absolute path for listing
            if word.startswith("~/") or word == "~":
                base = os.path.expanduser(word)
            elif word.startswith("/"):
                base = word
            else:
                base = os.path.join(os.getcwd(), word)

            # Split into directory and partial filename
            if os.path.isdir(base):
                dir_part  = base
                file_part = ""
            else:
                dir_part  = os.path.dirname(base)
                file_part = os.path.basename(base)

            # Guard: dir must exist and be reachable before listing
            if not dir_part:
                dir_part = os.getcwd()
            if not os.path.isdir(dir_part):
                return
            try:
                entries = sorted(os.listdir(dir_part))
            except OSError:
                return

            for entry in entries:
                if file_part and not entry.startswith(file_part):
                    continue
                full   = os.path.join(dir_part, entry)
                is_dir = os.path.isdir(full)
                meta   = "[dir] " if is_dir else "[file]"
                prefix    = word[: len(word) - len(file_part)]
                completed = prefix + entry
                if is_dir:
                    completed += "/"
                yield _PTCompletion(
                    completed,
                    start_position=-len(word),
                    display=entry + ("/" if is_dir else ""),
                    display_meta=meta,
                )

else:
    _HackersCompleter = None  # type: ignore


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
        "/profile": "System profile | /profile memory — show learned facts | /profile edit — edit learned facts",
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
        "/exit":    "Save memory + exit Hackers AI",
    }

    def __init__(self, improve: bool = False):
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
        # User profile improver (active when --improve / -i flag is passed)
        self._improve_mode = improve
        self._improver = UserProfileImprover(model=self.model)
        if improve:
            created = self._improver.ensure_file()
            if created:
                print(c("cyan", f"  [improve] ✨ Created user profile: {IMPROVE_PROFILE_PATH}"))
            else:
                print(c("dim",  f"  [improve] Profile: {IMPROVE_PROFILE_PATH}"))
            self._improver.show()

        _sudo_user = os.environ.get("SUDO_USER")
        self.cwd = _REAL_HOME if _sudo_user else os.getcwd()

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
            self._tg.send(
                "🟢 <b>Hackers AI is online</b>\n"
                f"Host: <code>{self.profile.get('hostname','')}</code>  "
                f"IP: <code>{self.profile.get('ip','')}</code>\n"
                f"User: <code>{self.profile.get('whoami','')}</code>  "
                f"Root: <code>{self.profile.get('root','')}</code>\n"
                "Send any command to get started."
            )

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
            home = _REAL_HOME
            display = self.cwd
            if display == home:
                display = "~"
            elif display.startswith(home + "/"):
                display = "~" + display[len(home):]
        except Exception:
            display    = self.cwd
            _sudo_user = "root"
            _hostname  = "kali"

        # Build inline badge prefix — shown on the SAME line as the shell prompt.
        # These are short tags so they don't cause readline line-wrap issues.
        badge_parts = []
        if self.sticky_target:
            badge_parts.append(c("dim", f"[{self.sticky_target}]"))
        if self._mcp_client:
            badge_parts.append(c("cyan", f"[mcp:{self._mcp_client.name}]"))
        if self._tg.enabled and self._tg._thread and self._tg._thread.is_alive():
            badge_parts.append(c("dim", "[tg:on]"))

        badge_str = (" ".join(badge_parts) + " ") if badge_parts else ""

        # Full single-line prompt: badges + shell prompt — no embedded newlines.
        # Every ANSI sequence is wrapped in readline non-printing markers so
        # readline counts only the visible characters for cursor positioning.
        user_col = "red" if not self.run_as_user else "green"
        prompt = (
            badge_str +
            c("dim", "(") + c(user_col, _sudo_user) +
            c("dim", "㉿") + c("cyan", _hostname) +
            c("dim", ")-[") + c("yellow", display) +
            c("dim", "]") + c("white", "$ ")
        )
        if _READLINE_OK:
            return _rl_wrap(prompt)
        return prompt

    def _print_banner(self):
        print(c("green", BANNER))
        root_str = c("red", "● ROOT") if self.profile["root"] else c("yellow", "● USER")
        n_tools  = len(self.profile["available_tools"])
        print(c("dim", f"  Author   : IMApurbo  |  Agent v{VERSION}"))
        print(c("dim", f"  Model    : {self.model}"))
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
            _htop, _hbw = _box_top("HACKERS AI COMMANDS", "yellow")
            print(_htop)
            cats = [
                ("General",  ["/help","/clear","/history","/profile","/tools","/sysinfo","/switch","/exit"]),
                ("Session",  ["/target","/auth","/shell","/save","/dryrun"]),
                ("Recon",    ["/recon","/note","/notes","/delnotes"]),
                ("MCP",      ["/config","/mcp"]),
                ("Memory",   ["/improve"]),
                ("Notify",   ["/telegram"]),
            ]
            for cat, keys in cats:
                print(_box_row(c("white", cat), "║", _hbw))
                for k in keys:
                    v = self.SLASH_COMMANDS.get(k, "")
                    print(_box_row(f"  {c('cyan', k):<28} {v}", "║", _hbw))
            print(_box_bot(_hbw, "yellow"))
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
            sub = arg.strip().lower() if arg else ""

            # /profile memory — show learned user facts
            if sub == "memory":
                self._improver.show()
                return True

            # /profile edit — open profile file in editor
            if sub == "edit":
                editor = (
                    os.environ.get("VISUAL")
                    or os.environ.get("EDITOR")
                    or shutil.which("nano")
                    or shutil.which("vi")
                    or "nano"
                )
                self._improver.ensure_file()
                print(c("cyan", f"\n  Opening learned memory in {editor}..."))
                print(c("dim",  f"  File: {IMPROVE_PROFILE_PATH}"))
                print(c("dim",  "  One fact per line. Save and exit editor to apply changes.\n"))
                try:
                    subprocess.run([editor, IMPROVE_PROFILE_PATH])
                    print(c("green", "  ✓ Memory updated."))
                    self._improver.show()
                except Exception as e:
                    print(c("red", f"  Could not open editor: {e}"))
                    print(c("dim", f"  Edit manually: nano {IMPROVE_PROFILE_PATH}"))
                return True

            # /profile (no sub) — show system profile
            print()
            for k, v in self.profile.items():
                if k in {"available_tools","uname"}:
                    continue
                print(f"  {c('cyan', k+':'): <20} {v}")
            if self.sticky_target:
                print(f"  {c('cyan','sticky_target:'): <20} {self.sticky_target}")
            if self._mcp_client:
                print(f"  {c('cyan','active_mcp:'): <20} {self._mcp_client.name}")
            print(c("dim", "\n  Tip: /profile memory  — learned facts | /profile edit — edit them"))
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

        if slug == "/improve":
            self._improver.update(self.memory, verbose=True)
            return True

        if slug == "/exit":
            print(c("cyan", "\n  [memory] Saving learned facts from this session..."))
            try:
                changes = self._improver.update(self.memory, verbose=True)
                if changes == 0:
                    print(c("dim", "  [memory] Nothing new to save — profile already up to date."))
            except Exception as e:
                print(c("yellow", f"  [memory] Warning: could not update profile: {e}"))
            print(c("cyan", "\n  Goodbye.\n"))
            self._shutdown(skip_profile_update=True)
            sys.exit(0)

        return False

    def _shutdown(self, reason: str = "", skip_profile_update: bool = False):
        """Close every active connection cleanly: MCP server + Telegram bot."""
        # 1. Stop MCP subprocess and clear the DB record so _restore_mcp()
        #    does NOT reconnect it on the next startup.
        if self._mcp_client:
            try:
                name = self._mcp_client.name
                self._mcp_client._stop()
                print(c("dim", f"  [✓] MCP '{name}' disconnected"))
            except Exception:
                pass
            self._mcp_client = None
        # Always clear the persisted active-server record on any clean exit.
        try:
            self.memory.clear_mcp_active()
        except Exception:
            pass

        # 2. Update user profile on exit — skipped when /exit already ran it verbosely
        if not skip_profile_update:
            try:
                self._improver.update(self.memory, verbose=False)
            except Exception:
                pass

        # 3. Stop Telegram bridge
        if self._tg.enabled or (self._tg._thread and self._tg._thread.is_alive()):
            try:
                self._tg.stop(reason=reason) if reason else self._tg.stop()
                print(c("dim", "  [✓] Telegram bridge stopped"))
            except Exception:
                pass
        else:
            # Always call stop() to clean up the thread even if not "enabled"
            try:
                self._tg.stop()
            except Exception:
                pass

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
                print(c("dim",  f"  Config file: {MCP_CONFIG_PATH}"))
                if not os.path.exists(MCP_CONFIG_PATH):
                    print(c("yellow", "  (file does not exist yet — run /config to create it)"))
                else:
                    print(c("dim",  "  Run /config to edit it and add servers."))
                print()
                return True
            print()
            _mtop, _mbw = _box_top("MCP SERVERS (from ~/.hackers_ai_mcp.json)", "cyan")
            print(_mtop)
            _mi = _box_inner(_mbw)
            for name, scfg in servers.items():
                is_active = (name == active)
                if is_active and self._mcp_client:
                    # Check if the process is actually still alive
                    if self._mcp_client._proc and self._mcp_client._proc.poll() is not None:
                        act_badge = c("red", "● DEAD (run /mcp use to reconnect)")
                    else:
                        act_badge = c("green", "● ACTIVE")
                elif is_active:
                    act_badge = c("yellow", "● SAVED (not connected)")
                else:
                    act_badge = c("dim", "○")
                cmd_str   = scfg.get("command", "?")
                args      = scfg.get("args", [])
                args_str  = " ".join(str(a) for a in args[:4])
                if len(args) > 4:
                    args_str += " …"
                env_keys = list((scfg.get("env") or {}).keys())
                env_str  = f" env:[{','.join(env_keys)}]" if env_keys else ""
                # Row 1: name + status badge (always fits)
                print(_box_row(f"{c('white', name)}  {act_badge}", "║", _mbw))
                # Row 2+: command path + args + env, safe-wrapped so nothing overflows
                detail = f"  {cmd_str} {args_str}{env_str}".rstrip()
                for row in _box_safe(detail, _mbw, "║"):
                    print(row)
            print(_box_bot(_mbw, "cyan"))
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
                _t1top, _t1bw = _box_top(f"{sub1.upper()} TOOLS", "cyan")
                print(_t1top)
                _t1i = _box_inner(_t1bw)
                for t in tools[:25]:
                    schema = t.get("inputSchema", {}) or {}
                    props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
                    params = ", ".join(props.keys()) if props else ""
                    # First line: name + params (capped so it can't overflow)
                    name_part   = t["name"]
                    params_part = f"({params})" if params else "()"
                    # cap params display so name+params never exceeds inner width
                    max_params  = max(8, _t1i - len(name_part) - 3)
                    if len(params_part) > max_params:
                        params_part = params_part[:max_params - 1] + "…"
                    print(_box_row(c("white", f"{name_part:<22}") + c("dim", params_part), "║", _t1bw))
                    # Second line: description — strip newlines, wrap to fit
                    raw_desc = t.get("description", "").replace("\n", " ").replace("\r", "").strip()
                    if raw_desc:
                        # indent + cap at one wrapped line max (2 rows)
                        desc_lines = _box_safe("  " + raw_desc, _t1bw, "║", color="dim")
                        for dl in desc_lines[:2]:
                            print(dl)
                if len(tools) > 25:
                    print(_box_row(f"  … +{len(tools)-25} more tools  (use /mcp tools for full list)", "║", _t1bw))
                print(_box_bot(_t1bw, "cyan"))
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
            _temp_client = False
            if sub1:
                # Show tools for a named server without switching active
                cfg     = _mcp_config_load()
                servers = cfg.get("mcpServers", {})
                if sub1 not in servers:
                    print(c("red", f"  Server '{sub1}' not found in config."))
                    return True
                client = _mcp_client_from_config(sub1, servers[sub1])
                _temp_client = True
                print(c("dim", f"  Connecting to '{sub1}' for tool listing..."), end="", flush=True)
                try:
                    client.initialize()
                    print(c("green", " ok"))
                except Exception as e:
                    print(c("red", f" failed: {e}"))
                    client._stop()
                    return True
            if not client:
                print(c("yellow", "  No active MCP. Use /mcp use <name> first, or /mcp tools <name>."))
                return True
            tools = client.list_tools(force_refresh=True)
            if not tools:
                print(c("yellow", "  No tools returned by this server."))
                if _temp_client:
                    client._stop()
                return True
            srv_name = client.name
            print()
            _sttop, _stbw = _box_top(f"{srv_name.upper()} — {len(tools)} TOOLS", "cyan")
            print(_sttop)
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
                # Tool name row
                print(_box_row(c("white", t["name"]), "║", _stbw))
                # Description — sanitise newlines, safe-wrap all lines
                raw_desc = (t.get("description") or "").replace("\r", "").strip()
                for row in _box_safe(raw_desc, _stbw, "║", color="dim"):
                    print(row)
                # Params — safe-wrap in case param list is very long
                for row in _box_safe(f"params: {params_str}", _stbw, "║", color="dim"):
                    print(row)
            print(_box_bot(_stbw, "cyan"))
            print()
            if _temp_client:
                client._stop()
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
                    _tdw  = _tw()
                    _tdbw = _tdw + 4   # total_width for _box_row: prefix(2)+left+inner+right
                    print(c("dim", "  ┌" + "─"*(_tdw) + "┐"))
                    _td_inner = _box_inner(_tdbw)
                    _td_lines = (text or "(empty)").splitlines()
                    shown = 0
                    for raw_line in _td_lines[:80]:
                        # sanitise and hard-wrap each output line
                        clean = _strip_ansi_g(raw_line).rstrip()
                        while True:
                            if len(clean) <= _td_inner:
                                print(_box_row(clean, "│", _tdbw, pad_char=" "))
                                shown += 1
                                break
                            cut = clean.rfind(" ", 0, _td_inner)
                            cut = cut if cut > 0 else _td_inner
                            print(_box_row(clean[:cut], "│", _tdbw, pad_char=" "))
                            shown += 1
                            clean = clean[cut:].lstrip()
                    extra = len(_td_lines) - 80
                    if extra > 0:
                        print(_box_row(f"  … {extra} more lines not shown", "│", _tdbw))
                    print(c("dim", "  └" + "─"*(_tdw) + "┘"))
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
                _mrbw     = _tw() + 2
                _mr_inner = _mrbw - 4
                _mr_label = "─ MCP Result Summary "
                _mr_fill  = _mr_inner - len(_mr_label)
                print(c("green", "  ╭" + _mr_label + "─" * max(1, _mr_fill) + "╮"))
                for line in summary.splitlines():
                    print(_box_row(line, "│", _mrbw))
                print(c("green", "  ╰" + "─" * _mr_inner + "╯"))
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
                _tgtop, _tgbw = _box_top("TELEGRAM BOT", "cyan")
                print(_tgtop)
                print(_box_row(f"State   : {state}", "║", _tgbw))
                print(_box_row(f"Token   : {tok}", "║", _tgbw))
                print(_box_row(f"User ID : {uid}", "║", _tgbw))
                print(_box_row("", "║", _tgbw))
                print(_box_row("Send any message to your bot on Telegram and", "║", _tgbw))
                print(_box_row("Hackers AI will execute it and reply with output.", "║", _tgbw))
                print(_box_bot(_tgbw, "cyan"))
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
            self._tg.send(
                "🟢 <b>Hackers AI is online</b>\n"
                f"Host: <code>{self.profile.get('hostname','')}</code>  "
                f"IP: <code>{self.profile.get('ip','')}</code>\n"
                f"User: <code>{self.profile.get('whoami','')}</code>  "
                f"Root: <code>{self.profile.get('root','')}</code>\n"
                "Send any command to get started."
            )
            return True

        # ── stop ──────────────────────────────────────────────
        if sub == "stop":
            self._tg.stop(reason="⌨️ Operator ran /tg stop")
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
        # ── Telegram mode: send 3-button inline keyboard ──────────
        if getattr(self, "_tg_mode", False) and self._tg and self._tg.token:
            import uuid

            flush_fn = getattr(self._tg, "_force_flush", None)
            if flush_fn:
                flush_fn()
            time.sleep(0.05)

            cb_id = uuid.uuid4().hex[:12]
            evt   = threading.Event()
            self._tg._pending_confirms[cb_id] = {"event": evt, "result": False, "choice": "no"}

            tg_msg = (
                "⚠️ <b>SCOPE CONFIRMATION</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Target:</b> {host}\n"
                "This target is <b>not local</b> and not in your authorized list.\n\n"
                "Only test systems you <b>OWN</b> or have <b>WRITTEN PERMISSION</b> to test.\n"
                "Unauthorized testing is illegal.\n\n"
                "✅ <b>Yes (once)</b> — proceed this time, not saved\n"
                "💾 <b>Yes + Add</b> — proceed and add to authorized list\n"
                "❌ <b>No</b> — cancel (default)\n\n"
                "Do you confirm you have authorization?"
            )

            msg_id = self._tg.send_with_scope_keyboard(tg_msg, cb_id)

            tapped = evt.wait(timeout=120)
            entry  = self._tg._pending_confirms.pop(cb_id, {})
            choice = entry.get("choice", "no")

            if not tapped:
                if msg_id:
                    self._tg._edit_reply_markup(msg_id, "⏱ Timed out — task cancelled.")
                return False

            if choice == "add":
                self.memory.add_authorized_target(host)
                if msg_id:
                    self._tg._edit_reply_markup(msg_id, f"💾 Confirmed — {host} added to authorized list. Executing...")
                return True
            elif choice == "yes":
                if msg_id:
                    self._tg._edit_reply_markup(msg_id, "✅ Confirmed (once) — executing...")
                return True
            else:
                if msg_id:
                    self._tg._edit_reply_markup(msg_id, "❌ Cancelled.")
                return False

        # ── Terminal mode ──────────────────────────────────────────
        print()
        _sctop, _scbw = _box_top("SCOPE CONFIRMATION", "yellow")
        print(_sctop)
        print(_box_row(f"Target: {c('white', host)}", "║", _scbw))
        print(_box_row("This target is not local and not in your authorized list.", "║", _scbw))
        print(_box_row("", "║", _scbw))
        print(_box_row("Only test systems you OWN or have WRITTEN PERMISSION to test.", "║", _scbw))
        print(_box_row("Unauthorized testing is illegal.", "║", _scbw))
        print(_box_row("", "║", _scbw))
        print(_box_row("Options:", "║", _scbw))
        print(_box_row("  y = proceed this time (not saved)", "║", _scbw))
        print(_box_row(f"  a = proceed and add '{host}' to authorized list", "║", _scbw))
        print(_box_row("  n = cancel (default)", "║", _scbw))
        print(_box_bot(_scbw, "yellow"))
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
                icon  = "⚙" if stype == "COMMAND" else "🐍" if stype == "PYTHON" else "✏️" if stype == "SMART_EDIT" else "🔌"
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
        _eptop, _epbw = _box_top("EXECUTION PLAN", "yellow")
        print(_eptop)
        _ep_inner = _epbw - 2 - 2   # inner width for the ╠══ STEPS ╣ separator
        print(_box_row(f"{c('white','Summary')} : {plan.get('summary','N/A')}", "║", _epbw))
        print(_box_row(f"{c('white','Steps  ')} : {len(plan.get('steps', []))}", "║", _epbw))
        if self.sticky_target:
            print(_box_row(f"{c('cyan','Target ')} : {self.sticky_target}", "║", _epbw))
        if plan.get("requires_root"):
            print(_box_row(c("red", "⚠ Requires root"), "║", _epbw))
        warn = plan.get("warning")
        if warn and str(warn).lower() not in ("null","none",""):
            print(_box_row(f"{c('red','⚡ WARNING')}: {warn}", "║", _epbw))
        _steps_label = "══ STEPS "
        print(c("yellow", f"  ╠{_steps_label}{chr(9552) * max(1, _ep_inner - len(_steps_label))}╣"))
        for s in plan.get("steps", []):
            stype = s.get("type","command").upper()
            label = (s.get("command") or s.get("description") or "")[:82]
            tc    = "cyan" if stype == "COMMAND" else "magenta" if stype == "PYTHON" else "green" if stype == "SMART_EDIT" else "dim"
            print(_box_row(f"[{c(tc, f'{stype:<8}')}] {label}", "║", _epbw))
        print(_box_bot(_epbw, "yellow"))
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
        # ── Fit content inside the border ─────────────────────────
        # bw = total box width (prefix "  " + left border + inner + right border)
        # inner content width = bw - len("  ") - 4  =  bw - 6
        bw = _tw() + 2          # e.g. tw=78 → bw=80
        inner = bw - 6          # e.g. 74  (used for wrapping)
        # border row inner (between │ chars) = bw - 2(prefix) - 4 = bw-6  ✓ same
        # top/bottom line inner (between ╭ and ╮) = bw - 2(prefix) - 2 = bw-4
        top_inner = bw - 4      # e.g. 76
        label     = "─ Hackers AI "
        fill      = top_inner - len(label)   # dashes after label up to ╮

        def _strip_ansi(s: str) -> str:
            return re.sub(r"\033\[[0-9;]*m", "", s)

        def _wrap_line(line: str) -> list:
            visible = _strip_ansi(line)
            if len(visible) <= inner:
                return [line]
            indent = len(visible) - len(visible.lstrip())
            wrapped = textwrap.wrap(
                visible,
                width=inner,
                subsequent_indent=" " * indent,
                break_long_words=True,
                break_on_hyphens=False,
            )
            return wrapped if wrapped else [line]

        print()
        print(c("green", "  ╭") + c("green", label) + c("dim", "─" * max(1, fill)) + c("green", "╮"))
        for raw_line in text.splitlines():
            for wrapped in _wrap_line(raw_line):
                print(_box_row(wrapped, "│", bw))
        print(c("green", "  ╰" + "─" * top_inner + "╯"))
        print()

    def _inject_profile_context(self):
        self.profile["cwd"] = self.cwd
        self.profile["real_home"] = _REAL_HOME
        self.profile["sticky_target"] = self.sticky_target or "(none set)"
        # Inject learned user-profile facts so the planner can personalise responses
        self.profile["user_profile_facts"]    = self._improver.inject_into_prompt()
        self.profile["user_preferred_lang"]   = self._improver.preferred_language()
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
        Explicit tool catalogue with REQUIRED/OPTIONAL labels and full JSON
        example args so the LLM generates correct args on the first try.

        Format per tool:
          TOOL: tool_name
          DESC: tool description
          ARGS:
            REQUIRED  param_name (type): description
            OPTIONAL  param_name (type): description
          EXAMPLE: {"param": "value"}
          ──────────────────────────
        """
        tools = client.list_tools()
        if not tools:
            return "(no tools)"
        lines = []
        lines.append("=" * 60)
        lines.append("MCP TOOL CATALOGUE — USE EXACT PARAM NAMES BELOW")
        lines.append("WARNING: Do NOT add offset/count/pagination unless listed here.")
        lines.append("=" * 60)
        for t in tools:
            name   = t.get("name", "?")
            tdesc  = (t.get("description") or "").replace("\n", " ").strip()
            schema = t.get("inputSchema") or {}
            props  = schema.get("properties", {}) if isinstance(schema, dict) else {}
            req    = set(schema.get("required", [])) if isinstance(schema, dict) else set()

            lines.append(f"\nTOOL: {name}")
            lines.append(f"DESC: {tdesc[:120]}")

            if props:
                lines.append("ARGS:")
                example_args = {}
                for pname, pinfo in (props.items() if isinstance(props, dict) else []):
                    label = "REQUIRED" if pname in req else "OPTIONAL"
                    if isinstance(pinfo, dict):
                        ptype = pinfo.get("type", "any")
                        if pinfo.get("enum"):
                            ptype = "|".join(str(e) for e in pinfo["enum"][:6])
                        pdesc = pinfo.get("description", "").replace("\n", " ").strip()[:80]
                        # Build example value
                        if pinfo.get("enum"):
                            example_args[pname] = pinfo["enum"][0]
                        elif ptype == "integer":
                            example_args[pname] = 0
                        elif ptype == "boolean":
                            example_args[pname] = False
                        elif ptype == "array":
                            example_args[pname] = []
                        else:
                            example_args[pname] = f"<{pname}>"
                    else:
                        ptype = "any"
                        pdesc = ""
                        example_args[pname] = f"<{pname}>"
                    lines.append(f"  {label:<8} {pname} ({ptype}): {pdesc}")
                # Only show required args in example to keep it clean
                req_example = {k: v for k, v in example_args.items() if k in req}
                if not req_example:
                    req_example = example_args  # fall back to all if none required
                lines.append(f"EXAMPLE: {json.dumps(req_example)}")
            else:
                lines.append("ARGS: (none — call with empty args {})")
                lines.append("EXAMPLE: {}")
            lines.append("─" * 50)
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
        analyzer = QueryAnalyzer(model=self.model, mcp_active=bool(self._mcp_client))
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
            _nibw = _tw() + 2
            _ni_inner = _nibw - 4
            _ni_label = "─ Need more info "
            _ni_fill  = _ni_inner - len(_ni_label)
            print(c("yellow", "  ╭" + _ni_label + "─" * max(1, _ni_fill) + "╮"))
            print(_box_row(c("yellow", question), "│", _nibw))
            print(c("yellow", "  ╰" + "─" * _ni_inner + "╯"))
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
        _elapsed  = _task_end - _task_start
        summary = Summarizer(model=self.model).summarize(raw, user_input, history, self.profile)
        self._print_response(summary)
        self.memory.add_message("user",      user_input, self.model)
        self.memory.add_message("assistant", summary,    self.model)

        # Telegram task-done notification (tasks only — informational handled above)
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
                _snbw     = _tw() + 2
                _sn_inner = _snbw - 4
                _sn_label = "─ Suggested next "
                _sn_fill  = _sn_inner - len(_sn_label)
                print(c("dim", "  ╭" + _sn_label + "─" * max(1, _sn_fill) + "╮"))
                for i, s in enumerate(lines, 1):
                    print(_box_row(c("cyan", f"  {i}.") + f" {s}", "│", _snbw))
                print(c("dim", "  ╰" + "─" * _sn_inner + "╯"))
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

        # ── prompt_toolkit session (rich completions + annotations) ──
        _pt_session = None
        if _PT_OK and _HackersCompleter is not None:
            from prompt_toolkit.key_binding import KeyBindings as _PTKeyBindings
            from prompt_toolkit.filters import completion_is_selected as _pt_sel

            _kb = _PTKeyBindings()

            @_kb.add("tab")
            def _tab_accept(event):
                """Tab: accept highlighted completion; if none highlighted yet,
                move to first item so the next Tab accepts it."""
                buf = event.app.current_buffer
                if buf.complete_state:
                    if buf.complete_state.current_completion is not None:
                        buf.apply_completion(buf.complete_state.current_completion)
                    else:
                        buf.complete_next()
                else:
                    buf.start_completion(select_first=True)

            @_kb.add("right", filter=_pt_sel)
            def _right_accept(event):
                """→ key: accept current completion when one is highlighted."""
                buf = event.app.current_buffer
                if buf.complete_state and buf.complete_state.current_completion is not None:
                    buf.apply_completion(buf.complete_state.current_completion)

            _pt_style = _PTStyle.from_dict({
                "completion-menu.completion":              "bg:#1e1e2e #cdd6f4",
                "completion-menu.completion.current":      "bg:#313244 #cba6f7 bold",
                "completion-menu.meta.completion":         "bg:#1e1e2e #6c7086 italic",
                "completion-menu.meta.completion.current": "bg:#313244 #7f849c italic",
                "scrollbar.background":                    "bg:#313244",
                "scrollbar.button":                        "bg:#89b4fa",
            })
            _pt_session = _PTSession(
                completer=_HackersCompleter(),
                style=_pt_style,
                key_bindings=_kb,
                complete_while_typing=True,
                # Reserve 8 lines at the bottom so the dropdown floats above
                # output and never obscures the last responses.
                reserve_space_for_menu=8,
            )

        while True:
            try:
                # Blank line before prompt — breathing room between AI output and input
                print()
                if _pt_session is not None:
                    raw_prompt = self._get_prompt()
                    # Strip readline non-printing markers (\x01/\x02 aka ^A/^B)
                    # that _rl_wrap() adds — prompt_toolkit doesn't use them and
                    # renders them literally, corrupting the terminal display.
                    raw_prompt = raw_prompt.replace("\x01", "").replace("\x02", "")
                    user_input = _pt_session.prompt(_PTANSI(raw_prompt)).strip()
                else:
                    user_input = input(self._get_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print(c("cyan", "\n\n  Goodbye.\n"))
                self._shutdown(reason="⌨️ Session ended (Ctrl+C / EOF)")
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
                    target = _REAL_HOME
                elif target.startswith("~/"):
                    target = os.path.join(_REAL_HOME, target[2:])
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

    # ── CLI flags ──────────────────────────────────────────────
    _improve_mode = "-i" in sys.argv or "--improve" in sys.argv
    for _f in ("-i", "--improve"):
        while _f in sys.argv:
            sys.argv.remove(_f)

    cli = CLI(improve=_improve_mode)
    cli.run()
