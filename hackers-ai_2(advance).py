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
import glob
import difflib
import random
import time
import textwrap
import importlib.util
from datetime import datetime
from typing import Optional
from contextlib import nullcontext
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
    from prompt_toolkit.shortcuts import radiolist_dialog as _PTRadioDialog
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
        # pending freeform question: at most one at a time (ask_user tool)
        self._pending_text_reply: Optional[dict] = None   # {"waiting": bool, "event": Event, "answer": str}

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

    def send_with_choice_keyboard(self, text: str, callback_id: str, options: list) -> int:
        """Send a message with one inline button per option (ask_user tool).
        callback data values are the 0-based option index."""
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        if len(clean) > 3800:
            clean = clean[:3800] + "\n...[truncated]"
        markup = {
            "inline_keyboard": [
                [{"text": opt[:60], "callback_data": f"{callback_id}:{i}"}]
                for i, opt in enumerate(options)
            ]
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

    def ask_and_wait(self, question: str, timeout: int = 180) -> Optional[str]:
        """Send a freeform question and block until the user replies with a plain
        text message (intercepted in _poll_loop instead of treated as a new
        command). Returns None on timeout."""
        if not self.token or not self.user_id:
            return None
        flush_fn = getattr(self, "_force_flush", None)
        if flush_fn:
            flush_fn()
        self.send(f"❓ {question}\n\n<i>Reply with your answer.</i>")
        evt = threading.Event()
        self._pending_text_reply = {"waiting": True, "event": evt, "answer": None}
        tapped = evt.wait(timeout=timeout)
        answer = self._pending_text_reply.get("answer") if self._pending_text_reply else None
        self._pending_text_reply = None
        return answer if tapped else None

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
                    if self._pending_text_reply is not None and self._pending_text_reply.get("waiting"):
                        self._pending_text_reply["answer"]  = text
                        self._pending_text_reply["waiting"] = False
                        self._pending_text_reply["event"].set()
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
            # Expose force_flush on the bot so _confirm_scope can drain before blocking
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

    def ask_agentic(self, messages: list, system: str = "", tools: list = None) -> dict:
        """Multi-turn tool-calling call. Unlike ask(), this sends/receives full
        Anthropic-shaped message content (including tool_use / tool_result
        blocks) and returns the raw response body instead of extracted text."""
        payload = {
            "model":      self.model,
            "max_tokens": 4096,
            "messages":   messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        data    = json.dumps(payload).encode("utf-8")
        url     = f"{PROXY_BASE_URL}/v1/messages"
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         "local-proxy-key",
            "anthropic-version": "2023-06-01",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
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

# Single shared cap so every tool truncates its visible output the same way
# — run_shell's live stream and every other tool's result preview. Collapsed
# is a bare peek; expanded (toggled via /output <id>) is more but still
# capped — never dump the whole thing into the terminal.
TOOL_OUTPUT_PREVIEW_LINES = 4
EXPANDED_OUTPUT_LINES     = 80

COLORS = {
    "reset":   "\033[0m",  "bold":    "\033[1m",   "dim":     "\033[2m",
    "red":     "\033[91m", "green":   "\033[92m",  "yellow":  "\033[93m",
    "blue":    "\033[94m", "magenta": "\033[95m",  "cyan":    "\033[96m",
    "white":   "\033[97m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"

# ── Lightweight Markdown → ANSI renderer ──────────────────────────
# Just enough to make model output (headers, **bold**, `code`, bullets,
# fenced code blocks) render legibly in a terminal instead of showing
# the raw markdown syntax.
_MD_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_MD_BOLD        = re.compile(r'\*\*([^*\n]+)\*\*')
_MD_BULLET      = re.compile(r'^(\s*)[-*]\s+(.*)')
_MD_HEADER      = re.compile(r'^(#{1,6})\s+(.*)')

def _render_markdown_inline(line: str) -> str:
    line = _MD_INLINE_CODE.sub(lambda m: c("cyan", m.group(1)), line)
    line = _MD_BOLD.sub(lambda m: f"{COLORS['bold']}{m.group(1)}{COLORS['reset']}", line)
    return line

def render_markdown(text: str) -> str:
    out_lines = []
    in_fence  = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            # No background bar here — that's reserved for live command
            # execution. Inside prose, code just needs to read as code.
            out_lines.append(c("dim", "│ ") + c("cyan", raw))
            continue
        m = _MD_HEADER.match(raw)
        if m:
            out_lines.append(f"{COLORS['bold']}{COLORS['cyan']}{m.group(2)}{COLORS['reset']}")
            continue
        m = _MD_BULLET.match(raw)
        if m:
            out_lines.append(f"{m.group(1)}{c('cyan', '•')} {_render_markdown_inline(m.group(2))}")
            continue
        out_lines.append(_render_markdown_inline(raw))
    return "\n".join(out_lines)

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

        # No "$ command" bar here — the ⏺ run_shell(...) bullet line printed by
        # the caller already shows the command, so this would just duplicate it.

        def _kill():
            if process and process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except (subprocess.TimeoutExpired, KeyboardInterrupt):
                        # A second Ctrl+C while we're waiting lands here too —
                        # swallow it locally and go straight to SIGKILL instead
                        # of leaking the interrupt back up mid-cleanup, which
                        # left the process group running and confused the CLI
                        # into reporting "Interrupted" without ever cancelling.
                        pass
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
            _live_cap      = TOOL_OUTPUT_PREVIEW_LINES
            _idle_spinner  = None
            _last_activity = time.time()
            try:
                while True:
                    rlist, _, _ = select.select([process.stdout], [], [], 0.3)
                    if rlist:
                        line = process.stdout.readline()
                        if not line:
                            break  # EOF — process finished
                        if _idle_spinner:
                            _idle_spinner.stop()
                            _idle_spinner = None
                        line = line.rstrip()
                        if line:
                            if not _tg_mode:
                                if len(stdout_lines) < _live_cap:
                                    _print(c("dim", f"  │ {tag}") + c("white", line))
                                elif len(stdout_lines) == _live_cap:
                                    _print(c("dim", f"  │ {tag}… output truncated live, still running"))
                            stdout_lines.append(line)
                        _last_activity = time.time()
                    elif process.poll() is not None:
                        break  # process exited with nothing left buffered
                    elif (not _tg_mode and _idle_spinner is None
                          and time.time() - _last_activity > 1.5):
                        # No output for a while but still running — make that
                        # visible instead of looking frozen.
                        _idle_spinner = Spinner(f"{tag}still running")
                        _idle_spinner.start()
            except KeyboardInterrupt:
                if _idle_spinner:
                    _idle_spinner.stop()
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

            if _idle_spinner:
                _idle_spinner.stop()
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
            elif not success:
                # Success is silent — only surface a footer when something
                # actually went wrong.
                _print(c("red", f"  └─ {tag}✗ exit:{process.returncode} ({elapsed}s)"))
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
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=filepath, tofile=filepath, n=2,
        ))
        return {
            "success": True, "stdout": summary,
            "stderr": "; ".join(errors) if errors else "",
            "ops_applied": applied,
            "returncode": 0, "elapsed": round(time.time() - t0, 2),
            "cancelled": False, "command": tag, "diff": diff,
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
# SECTION 10 — AGENT LOOP
# One continuous loop: the model sees the full conversation + tool
# results and decides what to do next each turn — planning, retrying
# after a failure, and producing the final answer are all just more
# turns of the same loop, not separate LLM-call stages.
# ══════════════════════════════════════════════════════════════

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

MAX_AGENT_TURNS = 30

TOOL_SCHEMAS = [
    {
        "name": "run_shell",
        "description": (
            "Run a shell command on the Linux system and return its stdout/stderr/exit "
            "code. Never use 'cd' — it is a shell builtin and each call is a fresh "
            "process; use absolute paths instead. Never prefix with sudo — you already "
            "have root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a surgical edit to an existing file on disk, described in natural "
            "language. Only the necessary lines are changed — the rest of the file is "
            "left untouched. Always use this instead of run_shell/write_file to modify "
            "an existing file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file to edit."},
                "instruction": {"type": "string", "description": "Precise natural-language description of the change(s) to make."},
            },
            "required": ["path", "instruction"],
        },
    },
    {
        "name": "mcp_call",
        "description": (
            "Call a tool on the currently active MCP server. Only usable when an MCP "
            "server is connected — see the MCP TOOL CATALOGUE in the system prompt for "
            "available tool names and exact argument keys."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "Exact MCP tool name from the catalogue."},
                "args": {"type": "object", "description": "Arguments matching the tool's schema."},
            },
            "required": ["tool"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from disk with line numbers. Prefer this over run_shell's cat "
            "for any file you intend to reason about. For large files, use offset/limit "
            "to read a specific range instead of the whole thing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string",  "description": "Absolute (or cwd-relative) path to the file."},
                "offset": {"type": "integer", "description": "1-based line number to start reading from. Optional."},
                "limit":  {"type": "integer", "description": "Max number of lines to read. Optional, default 2000."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or fully overwrite an existing one with the given "
            "content. To change only part of an existing file, use edit_file instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Absolute (or cwd-relative) path to write."},
                "content": {"type": "string", "description": "Complete file content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Search file contents for a regex pattern across a directory (or a single "
            "file). Prefer this over run_shell's grep/find for locating code or text — "
            "it returns matching file:line:text directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path":    {"type": "string", "description": "Directory or file to search. Defaults to the current working directory."},
                "glob":    {"type": "string", "description": "Optional filename glob to restrict the search, e.g. '*.py'."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "description": (
            "Find files matching a name pattern (e.g. '**/*.conf'), most recently "
            "modified first. Prefer this over run_shell's find for locating files by name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py' or 'src/*.js'."},
                "path":    {"type": "string", "description": "Base directory to search from. Defaults to the current working directory."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch a URL over HTTP(S) and return its text content (HTML tags stripped). "
            "Useful for reading documentation, advisories, or CVE pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to fetch, including scheme."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question when you are genuinely blocked on a "
            "decision only they can make (missing target, ambiguous scope, a choice "
            "between approaches). Prefer to act and figure things out yourself when "
            "you can — only ask when you truly cannot proceed without their input."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask."},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional short list of suggested answers shown as buttons/numbered choices.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "update_todos",
        "description": (
            "Show the user your current step-by-step plan and progress for a "
            "multi-step task. Pass the FULL current list each call — it replaces the "
            "previous one. Skip this for simple one-shot tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text":   {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["text", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
    {
        "name": "run_subagent",
        "description": (
            "Delegate an isolated, self-contained sub-task to a fresh agent instance "
            "with its own clean context (no access to this conversation's history). "
            "Use this for a task that would otherwise pollute your context with a lot "
            "of exploratory noise (e.g. 'find which file defines X', 'summarize this "
            "500-line log'). Returns only the subagent's final answer. A subagent "
            "cannot itself spawn further subagents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Self-contained description of the task — the subagent has no memory of this conversation, so include all needed context.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "notify_user",
        "description": (
            "Send a short desktop/Telegram notification to get the user's attention "
            "— e.g. a long scan just finished. This does not replace your final "
            "answer; use it only as an interim ping during a long-running task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Short notification text."},
            },
            "required": ["message"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}

# Some backends occasionally ignore the `tools` schema entirely and fall back
# to the classic ReAct text convention ("Action: tool\nAction Input: {...}")
# instead of a real tool_use block. Recognise that and still execute it,
# rather than silently printing it as though it were the final answer.
_REACT_ACTION_RE = re.compile(r'Action\s*:\s*([A-Za-z_][\w-]*)', re.IGNORECASE)
_REACT_INPUT_RE  = re.compile(r'Action\s*Input\s*:\s*(.*)', re.IGNORECASE | re.DOTALL)


def _parse_react_fallback(text: str):
    """Returns (tool_name, tool_input_dict) if `text` looks like a
    malformed 'Action: / Action Input:' tool call, else None."""
    m_action = _REACT_ACTION_RE.search(text)
    m_input  = _REACT_INPUT_RE.search(text)
    if not m_action or not m_input:
        return None
    name = m_action.group(1).strip()
    if name not in _TOOL_NAMES:
        return None
    parsed = extract_json(m_input.group(1).strip())
    if not isinstance(parsed, dict):
        return None
    return name, parsed


class SafetyGate:
    """Hard gate checked before every tool call — outside the model's own
    reasoning, same role Claude Code's permission system plays. Not something
    the model can talk its way past."""

    _DENY_PATTERNS = [
        re.compile(r'\brm\s+-rf\s+/(?:\s|$)'),
        re.compile(r'\bmkfs\.\w+\s+/dev/'),
        re.compile(r'\bdd\s+[^\n]*of=/dev/(sd|nvme|hd|vd)[a-z]?\d*\b'),
        re.compile(r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:'),  # fork bomb
    ]

    # Tools that actually change state or run commands — these are the ones
    # dry_run should suppress. Read-only/utility tools (read_file, grep, glob,
    # web_fetch, ask_user, update_todos, notify_user) still work in dry_run.
    _DRY_RUN_GATED = {"run_shell", "edit_file", "write_file", "mcp_call"}

    def __init__(self, scope_guard: "ScopeGuard"):
        self.scope_guard = scope_guard

    _SCOPE_CHECKED_TOOLS = ("run_shell", "web_fetch")

    @staticmethod
    def _target_text(tool_name: str, tool_input: dict) -> str:
        if tool_name == "run_shell":
            return tool_input.get("command", "")
        if tool_name == "web_fetch":
            return f"fetch {tool_input.get('url', '')}"
        if tool_name == "mcp_call":
            return json.dumps(tool_input.get("args", {}))
        return ""

    def check(self, tool_name: str, tool_input: dict, cli: "CLI") -> tuple:
        """Returns (allowed: bool, reason: str)."""
        if cli.dry_run and tool_name in self._DRY_RUN_GATED:
            return False, f"DRY RUN — not executed. Would call {tool_name}({json.dumps(tool_input)[:200]})"

        text = self._target_text(tool_name, tool_input)
        if tool_name in self._SCOPE_CHECKED_TOOLS:
            for pat in self._DENY_PATTERNS:
                if pat.search(text):
                    return False, "Blocked: command matches a destructive pattern and was not run."
            allowed, _reason, host = self.scope_guard.check(text, cli.sticky_target)
            if allowed is None:
                if not cli._confirm_scope(host):
                    return False, f"User declined authorization for target '{host}'."
        return True, ""


# ── Tool implementations ────────────────────────────────────────
# Each wraps an existing engine (CommandExecutor, SmartFileEditor, ...).
# Robustness that belongs to the tool itself (auto-install a missing
# binary, normalise a "not found" error) stays here. Robustness that
# belongs to the AGENT (retry differently, try another tool) is no
# longer hand-coded — the model sees the tool_result and decides.

_SHELL_SAFE_BUILTINS = {
    "echo", "cat", "ls", "rm", "mv", "cp", "mkdir", "chmod", "chown", "touch",
    "grep", "awk", "sed", "find", "curl", "wget", "ping", "ssh", "scp", "tar",
    "zip", "unzip", "python3", "python", "bash", "sh", "which", "true", "false",
    "cd", "source", "export", "alias", "unalias", "set", "unset", "exit", "exec",
    "eval", "read", "printf", "type", "help", "history", "jobs", "fg", "bg",
    "wait", "trap", "return", "break", "continue", "shift", "getopts", "umask", "ulimit",
}

_GUI_COMMANDS = {
    "firefox", "chromium", "chromium-browser", "google-chrome", "brave-browser",
    "xdg-open", "nautilus", "thunar", "dolphin", "vlc", "mpv", "gimp", "inkscape",
    "libreoffice", "evince", "code", "wireshark", "burpsuite", "zaproxy",
    "gedit", "mousepad", "kate", "discord", "slack", "telegram",
}


def _run_shell_tool(cli: "CLI", command: str) -> str:
    if not command or not command.strip():
        return "Empty command."
    cmd_exec = CommandExecutor()
    effective_cwd = cli.cwd if (cli.cwd and os.path.isdir(cli.cwd)) else None
    if '~' in command:
        command = re.sub(r'(?<![A-Za-z0-9_])~(?=/|\s|$)', _REAL_HOME, command)

    bin_name = command.strip().split()[0].split("/")[-1] if command.strip() else ""
    if bin_name and not shutil.which(bin_name) and bin_name not in _SHELL_SAFE_BUILTINS:
        return f"'{bin_name}' is not installed on this system."

    if getattr(cli, "_tg_mode", False) and bin_name in _GUI_COMMANDS:
        try:
            subprocess.Popen(
                CommandExecutor.as_user(command), shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, cwd=effective_cwd or None,
            )
            return f"Launched in background: {command[:100]}"
        except Exception:
            pass  # fall through to normal (foreground) execution
    elif bin_name in _GUI_COMMANDS:
        command = CommandExecutor.as_user(command)

    result = cmd_exec.run(command, cwd=effective_cwd)
    out = result["stdout"][:4000] or result["stderr"][:1000]
    low = (result["stdout"] + result["stderr"]).lower()
    _soft_pats = ("no such file or directory", "cannot access", "not found",
                  "no matches found", "0 directories, 0 files", "total 0")
    if not result["success"] and any(p in low for p in _soft_pats):
        return out or "Path not found or empty."
    status = "OK" if result["success"] else f"FAILED (exit {result['returncode']})"
    stderr_part = f"\nstderr: {result['stderr'][:1000]}" if result["stderr"] and not result["success"] else ""
    return f"[{status}]\n{out}{stderr_part}"


def _print_diff(diff_text: str):
    print()
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            print(c("green", f"  {line}"))
        elif line.startswith("-") and not line.startswith("---"):
            print(c("red", f"  {line}"))
        elif line.startswith("@@"):
            print(c("cyan", f"  {line}"))
        else:
            print(c("dim", f"  {line}"))
    print()


def _run_edit_file_tool(cli: "CLI", path: str, instruction: str) -> str:
    if not path:
        return "No path given."
    if not os.path.isabs(path) and cli.cwd:
        path = os.path.join(cli.cwd, path)
    result = SmartFileEditor(model=cli.model).edit(path, instruction)
    diff = result.get("diff", "")
    if not result["success"]:
        return f"FAILED: {result['stderr']}"
    if diff and not getattr(cli, "_tg_mode", False):
        _print_diff(diff)
    return f"{result['stdout']}\n\n{diff[:3000]}" if diff else result["stdout"]


def _run_mcp_call_tool(cli: "CLI", tool_name: str, args: dict) -> str:
    mcp_client = cli._mcp_client
    if not mcp_client:
        return "No active MCP client — connect one with /mcp use <name> first."
    if not tool_name:
        return "No tool name given."

    schema = {}
    for t in (mcp_client.list_tools() or []):
        if t.get("name") == tool_name:
            schema = t.get("inputSchema") or {}
            break
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if props:
        valid_keys = set(props.keys())
        args = {k: v for k, v in (args or {}).items() if k in valid_keys}

    try:
        raw = mcp_client.call_tool(tool_name, args, timeout=120)
    except Exception as e:
        return f"MCP call exception: {e}"
    text   = MCPStdioClient.extract_text_result(raw)
    is_err = raw.get("isError", False)
    cli.memory.log_mcp_call(mcp_client.name, tool_name, json.dumps(args), text[:500],
                             "error" if is_err else "success")
    return f"MCP tool error: {text[:1500]}" if is_err else text[:4000]


def _resolve_path(cli: "CLI", path: str) -> str:
    if path and not os.path.isabs(path) and cli.cwd:
        return os.path.join(cli.cwd, path)
    return path


def _run_read_file_tool(cli: "CLI", path: str, offset: int = None, limit: int = None) -> str:
    if not path:
        return "No path given."
    path = _resolve_path(cli, path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Cannot read file: {e}"
    start = max(0, (offset or 1) - 1)
    end   = start + (limit or 2000)
    chunk = lines[start:end]
    if not chunk:
        return f"(empty range — file has {len(lines)} lines)"
    numbered = "".join(f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk))
    if not numbered.endswith("\n"):
        numbered += "\n"
    if end < len(lines):
        numbered += f"... ({len(lines) - end} more lines, use offset={end + 1} to continue)\n"
    return numbered


def _run_write_file_tool(cli: "CLI", path: str, content: str) -> str:
    if not path:
        return "No path given."
    path = _resolve_path(cli, path)
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content or "")
    except Exception as e:
        return f"Cannot write file: {e}"
    return f"Wrote {len(content or '')} bytes to {path}"


def _run_grep_tool(cli: "CLI", pattern: str, path: str = "", glob_pattern: str = "") -> str:
    if not pattern:
        return "No pattern given."
    search_path = _resolve_path(cli, path) if path else (cli.cwd or ".")
    if shutil.which("rg"):
        cmd = ["rg", "-n", "--no-heading", "-e", pattern]
        if glob_pattern:
            cmd += ["-g", glob_pattern]
        cmd.append(search_path)
    elif glob_pattern:
        cmd = ["grep", "-rn", "-E", "--include", glob_pattern, pattern, search_path]
    else:
        cmd = ["grep", "-rn", "-E", pattern, search_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (result.stdout or "").strip()
    except Exception as e:
        return f"grep failed: {e}"
    if not out:
        return "No matches."
    lines = out.splitlines()
    if len(lines) > 200:
        out = "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more matches)"
    return out


def _run_glob_tool(cli: "CLI", pattern: str, path: str = "") -> str:
    if not pattern:
        return "No pattern given."
    base = _resolve_path(cli, path) if path else (cli.cwd or ".")
    try:
        matches = glob.glob(os.path.join(base, pattern), recursive=True)
    except Exception as e:
        return f"glob failed: {e}"
    matches = [m for m in matches if os.path.isfile(m)]
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    if not matches:
        return "No files matched."
    shown = matches[:200]
    out = "\n".join(shown)
    if len(matches) > 200:
        out += f"\n... ({len(matches) - 200} more files)"
    return out


def _run_web_fetch_tool(cli: "CLI", url: str) -> str:
    if not url:
        return "No URL given."
    if not re.match(r'^https?://', url, re.IGNORECASE):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Hackers AI agent)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw   = resp.read(2_000_000)
            ctype = resp.headers.get_content_type()
    except Exception as e:
        return f"Fetch failed: {e}"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    if "html" in (ctype or ""):
        text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', text)
        # Turn block-level boundaries into real line breaks before stripping
        # tags — otherwise the whole page collapses into a single giant line.
        text = re.sub(r'(?i)<(br|/p|/div|/li|/tr|/h[1-6])\s*/?>', '\n', text)
        text = re.sub(r'(?s)<[^>]+>', ' ', text)
        text = text.replace("&nbsp;", " ")
        text = re.sub(r'[ \t]+', ' ', text)
        text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    return text[:8000]


def _ask_user_terminal(question: str, options: list) -> str:
    if options and _PT_OK:
        try:
            values = [(str(i), opt) for i, opt in enumerate(options)]
            choice = _PTRadioDialog(
                title="Question", text=question, values=values,
            ).run()
            if choice is None:
                return "(no answer — cancelled)"
            return options[int(choice)]
        except Exception:
            pass  # fall through to the plain terminal prompt below

    print()
    _qtop, _qbw = _box_top("Question", "cyan")
    print(_qtop)
    for row in _box_safe(question, _qbw, "║"):
        print(row)
    if options:
        print(_box_row("", "║", _qbw))
        for i, opt in enumerate(options, 1):
            for row in _box_safe(f"  {i}. {opt}", _qbw, "║"):
                print(row)
    print(_box_bot(_qbw, "cyan"))
    try:
        ans = input(c("cyan", "  Your answer: ")).strip()
    except (EOFError, KeyboardInterrupt):
        return "(no answer — user interrupted)"
    if options:
        try:
            idx = int(ans) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
    return ans or "(no answer given)"


def _run_ask_user_tool(cli: "CLI", question: str, options: list) -> str:
    if not question:
        return "No question given."
    options = options or []

    if getattr(cli, "_tg_mode", False) and cli._tg and cli._tg.token:
        if options:
            import uuid
            cb_id = uuid.uuid4().hex[:12]
            evt   = threading.Event()
            cli._tg._pending_confirms[cb_id] = {"event": evt, "result": False, "choice": ""}
            text = (
                "❓ <b>Question</b>\n" + question + "\n\n"
                + "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
            )
            msg_id = cli._tg.send_with_choice_keyboard(text, cb_id, options)
            tapped = evt.wait(timeout=180)
            entry  = cli._tg._pending_confirms.pop(cb_id, {})
            if not tapped:
                if msg_id:
                    cli._tg._edit_reply_markup(msg_id, "⏱ Timed out — no answer.")
                return "(no answer — timed out)"
            try:
                answer = options[int(entry.get("choice", ""))]
            except (ValueError, IndexError):
                answer = entry.get("choice", "")
            if msg_id:
                cli._tg._edit_reply_markup(msg_id, f"✅ {answer}")
            return answer
        answer = cli._tg.ask_and_wait(question, timeout=180)
        return answer if answer else "(no answer — timed out)"

    return _ask_user_terminal(question, options)


def _run_update_todos_tool(cli: "CLI", todos: list) -> str:
    if not todos:
        return "Empty todo list."
    _icons = {"pending": "○", "in_progress": "◐", "completed": "●"}
    lines = [f"  {_icons.get(t.get('status', 'pending'), '○')} {t.get('text', '')}" for t in todos]
    rendered = "\n".join(lines)
    if getattr(cli, "_tg_mode", False):
        cli._tg.send(f"📝 <b>Plan</b>\n<pre>{rendered}</pre>")
    else:
        print()
        _ttop, _tbw = _box_top("Plan", "magenta")
        print(_ttop)
        for ln in lines:
            for row in _box_safe(ln, _tbw, "║"):
                print(row)
        print(_box_bot(_tbw, "magenta"))
    return f"Shown to user — {len(todos)} item(s)."


def _run_notify_user_tool(cli: "CLI", message: str) -> str:
    if not message:
        return "No message given."
    if cli._tg and cli._tg.enabled:
        cli._tg.send(f"🔔 {message}")
    if not getattr(cli, "_tg_mode", False) and shutil.which("notify-send"):
        try:
            cmd = f"notify-send 'Hackers AI' {shlex.quote(message[:200])}"
            subprocess.run(
                CommandExecutor.as_user(cmd), shell=True, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    print(c("cyan", f"\n  🔔 {message}"))
    return "Notification sent."


def _run_subagent_tool(cli: "CLI", task: str) -> str:
    if not task:
        return "No task given."
    print(c("magenta", f"\n  [Subagent] ⤷ {task[:100]}"))
    messages = [{"role": "user", "content": task}]
    system   = build_system_prompt(cli)
    gate     = SafetyGate(cli.scope_guard)
    agent    = FreeLLM(model=cli.model)
    return _run_tool_loop(cli, messages, system, gate, agent, allow_subagent=False)


def dispatch_tool(cli: "CLI", name: str, tool_input: dict) -> str:
    try:
        if name == "run_shell":
            return _run_shell_tool(cli, tool_input.get("command", ""))
        if name == "edit_file":
            return _run_edit_file_tool(cli, tool_input.get("path", ""), tool_input.get("instruction", ""))
        if name == "mcp_call":
            return _run_mcp_call_tool(cli, tool_input.get("tool", ""), tool_input.get("args", {}) or {})
        if name == "read_file":
            return _run_read_file_tool(cli, tool_input.get("path", ""), tool_input.get("offset"), tool_input.get("limit"))
        if name == "write_file":
            return _run_write_file_tool(cli, tool_input.get("path", ""), tool_input.get("content", ""))
        if name == "grep":
            return _run_grep_tool(cli, tool_input.get("pattern", ""), tool_input.get("path", ""), tool_input.get("glob", ""))
        if name == "glob":
            return _run_glob_tool(cli, tool_input.get("pattern", ""), tool_input.get("path", ""))
        if name == "web_fetch":
            return _run_web_fetch_tool(cli, tool_input.get("url", ""))
        if name == "ask_user":
            return _run_ask_user_tool(cli, tool_input.get("question", ""), tool_input.get("options", []) or [])
        if name == "update_todos":
            return _run_update_todos_tool(cli, tool_input.get("todos", []) or [])
        if name == "run_subagent":
            return _run_subagent_tool(cli, tool_input.get("task", ""))
        if name == "notify_user":
            return _run_notify_user_tool(cli, tool_input.get("message", ""))
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool '{name}' raised an exception: {e}"


def build_system_prompt(cli: "CLI") -> str:
    profile   = cli.profile
    tools_str = ", ".join(profile.get("available_tools", [])[:40]) or "standard linux tools"

    httpx_type = ToolInspector().identify_httpx()
    httpx_note = ""
    if httpx_type == "scanner":
        httpx_note = ("\nNOTE: httpx on this system = projectdiscovery scanner "
                       "(supports -title, -tech-detect, -status-code, -l <file>)")
    elif httpx_type == "client":
        httpx_note = ("\nNOTE: httpx on this system = encode HTTP client (curl-like). "
                       "Do NOT use scanner flags like -title or -tech-detect. Use curl for single-URL requests.")

    wl_finder    = WordlistFinder()
    wordlist_str = wl_finder.format_for_prompt(wl_finder.scan())

    user_profile_block = profile.get("user_profile_facts", "")
    _user_name, _ai_nickname = None, None
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
    identity_lines = ["You are the AI assistant in this conversation."]
    identity_lines.append(
        f"The user has given you the nickname '{_ai_nickname}' — use it as your own name."
        if _ai_nickname else
        "If the user gives you a nickname, accept it immediately and use it as your own name."
    )
    identity_lines.append(
        f"The user's name is {_user_name} — address them by name naturally."
        if _user_name else
        "If the user tells you their name, use it naturally going forward."
    )
    identity_block = "\n".join(identity_lines)

    mcp_block = ""
    if cli._mcp_client:
        mcp_block = (
            f"\n╔══ ACTIVE MCP SERVER: {cli._mcp_client.name} ══╗\n"
            "Use the mcp_call tool for anything covered below. Use run_shell for "
            "anything else. Never ask the user for missing context — use a tool to discover it.\n\n"
            + CLI._build_mcp_schema_text(cli._mcp_client)
        )

    web_block = textwrap.dedent(f"""
═══ WEB TESTING — PRIORITY ORDER ═══
For web/XSS/injection testing, follow this order:
PHASE 1 — curl first, always:
  curl -si 'URL' | head -60
  Test reflection with payloads such as: {', '.join(CURL_XSS_PAYLOADS[:6])} ...
  Try URL-encoded variants if raw payloads are filtered. Check response headers (CSP,
  X-XSS-Protection) too.
PHASE 2 — if curl confirms reflection but not execution: dalfox url 'URL'
PHASE 3 — if dalfox is insufficient: xsstrike -u 'URL'
PHASE 4 — template scan for known CVEs: nuclei with xss templates
""").strip()

    return textwrap.dedent(f"""
⚠ OUTPUT RULE: Write EVERY word using ONLY plain English/Latin letters (a-z A-Z).
No Bengali/Arabic/Chinese/Devanagari/Cyrillic script. If the user speaks another
language, respond phonetically in Latin letters (e.g. "kemon acho", "kya haal hai").
If ANY non-Latin character appears in your output it is a critical failure.

{identity_block}
NEVER call the user by your own nickname. NEVER call yourself by the user's name.

You are Hackers AI — an autonomous Linux agent with full shell access, developed by
AKM Korishee Apurbo (@IMApurbo). You are warm and casual, not a cold robot. For pure
small talk, just chat back — do not steer the conversation toward hacking topics unless
the user brings it up first. Be concise. No markdown fences, no ** bold, no # headers.

{user_profile_block}
LIVE SYSTEM:
  Distro   : {profile.get('distro', 'Linux')}   Kernel: {profile.get('kernel', '')}   Arch: {profile.get('arch', '')}
  Hostname : {profile.get('hostname', '')}      User: {profile.get('whoami', '')}   Root: {profile.get('root', False)}
  CWD      : {cli.cwd}
  Target   : {cli.sticky_target or '(none set)'}
  Shell tools: {tools_str}{httpx_note}
{mcp_block}

{wordlist_str}

{web_block}

═══ RULES ═══
- You already have root — never prefix commands with sudo.
- Never use 'cd' — each run_shell call is a fresh process. Use absolute paths instead.
- All URLs and file paths in shell commands must be single-quoted.
- pip installs: always add --break-system-packages --quiet.
- To edit an EXISTING file, always use edit_file — never rewrite it wholesale with
  write_file/run_shell.
- Prefer the dedicated file tools over shelling out: read_file instead of cat, grep
  instead of shell grep/find -exec grep, glob instead of find, write_file instead of
  a shell redirect. Use run_shell for everything else (scans, installs, pentest tools).
- There is no dedicated Python-execution tool: to run a Python script, write it with
  write_file then execute it with run_shell (e.g. python3 /tmp/script.py).
- If run_shell reports a binary isn't installed, do NOT silently install it yourself.
  Tell the user it's missing and what would install it (e.g. "nikto isn't installed —
  run `apt-get install -y nikto` if you'd like me to use it"), or use ask_user if you
  need their go-ahead. Only run an install command yourself when the user has clearly
  already asked you to install something.
- Use web_fetch to read a URL's content directly instead of curl-then-parse when you
  only need the text (docs, advisories, CVE pages).
- Use ONLY wordlist paths listed above — never guess a path.
- When a tool call fails, read the error yourself and decide: retry differently, try a
  different tool, or explain the failure to the user. You do not need to ask before
  adjusting your own approach.
- Use ask_user only when truly blocked on something only the user can decide — prefer
  acting and discovering the answer yourself.
- Use update_todos to show progress on multi-step tasks (3+ real steps). Skip it for
  quick one-shot requests.
- Use run_subagent to offload a self-contained, exploratory sub-task (e.g. digging
  through a large log or an unfamiliar directory tree) so the noise doesn't fill up
  your own context — it only returns the final answer.
- Use notify_user sparingly, only for a genuinely long-running task the user is
  waiting on.
- The user already saw every command you ran and its output as it happened — don't
  re-paste the exact command or raw output in your final answer. Summarize findings.
- Once you have enough information to answer, respond with plain text and no further
  tool calls — that reply is shown to the user as the final answer, so make it a real
  summary of what you did and found, not a status update.
    """).strip()


# ── Output rendering: interleaved text, compact tool calls, expandable output ──

_TOOL_PRIMARY_ARG = {
    "run_shell": "command", "edit_file": "path", "mcp_call": "tool",
    "read_file": "path", "write_file": "path", "grep": "pattern",
    "glob": "pattern", "web_fetch": "url", "ask_user": "question",
    "run_subagent": "task", "notify_user": "message",
}

# run_shell streams its own output live via CommandExecutor — printing the
# truncated result preview afterward would just duplicate it on screen.
_SKIP_PREVIEW_TOOLS = {"run_shell"}

# Tools with no progress feedback of their own — worth a spinner. run_shell
# already streams live; ask_user/update_todos are interactive or instant;
# run_subagent prints its own rich nested output, so a spinner would clash.
_SPINNER_TOOLS = {"mcp_call", "web_fetch", "edit_file", "read_file", "write_file", "grep", "glob"}

_THINKING_VERBS = [
    "Thinking", "Reasoning", "Analyzing", "Investigating", "Working", "Scheming",
]


def _spinner_or_null(cli: "CLI", label: str):
    if getattr(cli, "_tg_mode", False):
        return nullcontext()
    return Spinner(label)


def _format_tool_call(name: str, tool_input: dict) -> str:
    key = _TOOL_PRIMARY_ARG.get(name)
    arg = tool_input.get(key) if key else None
    if arg is None:
        return f"{name}({json.dumps(tool_input)[:100]})"
    arg = str(arg).replace("\n", " ")
    if len(arg) > 100:
        arg = arg[:100] + "…"
    return f"{name}({arg})"


def _print_reasoning(cli: "CLI", text: str):
    text = text.strip()
    if not text:
        return
    if getattr(cli, "_tg_mode", False):
        cli._tg.send(text[:3000])
        return
    print()
    for line in render_markdown(text).splitlines():
        print(f"  {line}")


def _store_tool_output(cli: "CLI", text: str) -> int:
    cli._tool_output_log.append(text)
    return len(cli._tool_output_log) - 1


_ERROR_PREFIXES = ("FAILED", "MCP tool error", "MCP call exception", "Tool '", "Fetch failed", "grep failed", "glob failed")


def _print_tool_output(text: str, idx: int, budget_lines: int = TOOL_OUTPUT_PREVIEW_LINES):
    """Truncate to a fixed character budget sized to ~budget_lines terminal
    rows — not a raw newline count. A single giant line (e.g. fetched web
    text with no line breaks) and many short lines both end up the same
    handful of visible rows, instead of one bypassing the other's cap."""
    is_error = text.startswith(_ERROR_PREFIXES)
    color    = "red" if is_error else "white"
    width    = max(40, _tw() - 6)
    budget   = width * budget_lines

    lines, shown, used, truncated = text.splitlines() or [text], [], 0, False
    for ln in lines:
        if len(shown) >= budget_lines:
            truncated = True
            break
        remaining = budget - used
        if remaining <= 0:
            truncated = True
            break
        if len(ln) > remaining:
            shown.append(ln[:remaining] + "…")
            truncated = True
            break
        shown.append(ln)
        used += len(ln) + 1
    if len(shown) < len(lines):
        truncated = True

    for ln in shown:
        print(f"      {c(color, ln)}")
    if truncated:
        print(c("dim", f"      … (see /output {idx})"))


def _run_tool_loop(cli: "CLI", messages: list, system: str, gate: "SafetyGate",
                    agent: "FreeLLM", allow_subagent: bool) -> str:
    """Shared turn loop used by both the main agent and run_subagent. The only
    difference for a subagent is that it cannot itself call run_subagent —
    nesting is capped at one level deep."""
    tools = TOOL_SCHEMAS if allow_subagent else [
        t for t in TOOL_SCHEMAS if t["name"] != "run_subagent"
    ]

    for _turn in range(MAX_AGENT_TURNS):
        with _spinner_or_null(cli, random.choice(_THINKING_VERBS)):
            try:
                resp = agent.ask_agentic(messages, system=system, tools=tools)
            except Exception as e:
                return f"[LLM error] {e}"

        content = resp.get("content") or []
        if not content:
            return "(empty response from model)"
        messages.append({"role": "assistant", "content": content})

        tool_uses  = [b for b in content if b.get("type") == "tool_use"]
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]

        if not tool_uses:
            final_text = "\n".join(text_parts).strip()
            fallback = _parse_react_fallback(final_text)
            if not fallback:
                return final_text
            # The backend replied with "Action: / Action Input:" text instead
            # of a real tool_use block — rewrite it as one so it still runs,
            # and so the conversation history we send next turn stays sane.
            fb_name, fb_input = fallback
            fake_id  = f"fallback_{_turn}"
            fake_tu  = {"type": "tool_use", "id": fake_id, "name": fb_name, "input": fb_input}
            messages[-1] = {"role": "assistant", "content": [fake_tu]}
            tool_uses  = [fake_tu]
            text_parts = []

        # Commentary the model gave before deciding to call tools this turn.
        _print_reasoning(cli, "\n".join(text_parts))

        tool_results = []
        for tu in tool_uses:
            name       = tu.get("name", "")
            tool_input = tu.get("input", {}) or {}
            tid        = tu.get("id", "")

            print(c("cyan", f"\n  ⏺ {_format_tool_call(name, tool_input)}"))

            allowed, reason = gate.check(name, tool_input, cli)
            if not allowed:
                print(c("yellow", f"      blocked: {reason}"))
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tid,
                    "content": reason, "is_error": True,
                })
                continue

            if name in _SPINNER_TOOLS:
                with _spinner_or_null(cli, f"Running {name}"):
                    result_text = dispatch_tool(cli, name, tool_input)
            else:
                result_text = dispatch_tool(cli, name, tool_input)
            idx = _store_tool_output(cli, result_text)
            if getattr(cli, "_tg_mode", False):
                pass
            elif name not in _SKIP_PREVIEW_TOOLS:
                # run_shell already streamed (and possibly capped) its own
                # output live — nothing more to print here for it.
                _print_tool_output(result_text, idx)
            tool_results.append({
                "type": "tool_result", "tool_use_id": tid,
                "content": result_text[:8000],
            })
        messages.append({"role": "user", "content": tool_results})

    return "Stopped after reaching the maximum number of steps for this task."


def run_agent_loop(cli: "CLI", user_input: str) -> str:
    system   = build_system_prompt(cli)
    history  = cli.memory.get_history(MAX_HISTORY)
    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_input})

    gate  = SafetyGate(cli.scope_guard)
    agent = FreeLLM(model=cli.model)
    return _run_tool_loop(cli, messages, system, gate, agent, allow_subagent=True)

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
            "/output":  "Show full output of a truncated tool result",
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
        "/output":  "Show full output of a truncated tool result: /output <id>",
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
        # Full (untruncated) tool output, indexed for the /output <id> command
        self._tool_output_log: list = []
        # Per-id expand/collapse state — /output <id> toggles between them
        self._output_expanded: dict = {}
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
                ("General",  ["/help","/clear","/history","/output","/profile","/tools","/sysinfo","/switch","/exit"]),
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

        if slug == "/output":
            if not arg.strip().isdigit():
                print(c("red", "  Usage: /output <id>  (toggles a longer, still-capped view on/off)"))
            else:
                idx = int(arg.strip())
                if 0 <= idx < len(self._tool_output_log):
                    text     = self._tool_output_log[idx]
                    lines    = text.splitlines() or [text]
                    expanded = not self._output_expanded.get(idx, False)
                    self._output_expanded[idx] = expanded
                    print()
                    if expanded:
                        shown = lines[:EXPANDED_OUTPUT_LINES]
                        _otop, _obw = _box_top(f"Output #{idx} — expanded", "cyan")
                        print(_otop)
                        for row in _box_safe("\n".join(shown), _obw, "║"):
                            print(row)
                        if len(lines) > EXPANDED_OUTPUT_LINES:
                            print(_box_row(
                                c("dim", f"… +{len(lines) - EXPANDED_OUTPUT_LINES} more lines, still capped"),
                                "║", _obw))
                        print(_box_bot(_obw, "cyan"))
                        print(c("dim", f"  /output {idx} again to collapse."))
                    else:
                        print(c("dim", f"  Output #{idx} — collapsed:"))
                        for ln in lines[:TOOL_OUTPUT_PREVIEW_LINES]:
                            print(f"    {c('white', ln)}")
                        if len(lines) > TOOL_OUTPUT_PREVIEW_LINES:
                            print(c("dim", f"    … +{len(lines) - TOOL_OUTPUT_PREVIEW_LINES} lines "
                                            f"(/output {idx} again to expand)"))
                    print()
                else:
                    print(c("red", f"  No stored output #{idx}."))
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
        # Plain flowing text, no box border — matches how Claude Code itself
        # renders a final answer (tool calls get the ⏺ marker, the answer is
        # just text, rendered from markdown instead of shown raw).
        print()
        for line in render_markdown(text).splitlines():
            print(f"  {line}")
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

        _task_start = time.time()
        response    = run_agent_loop(self, user_input)
        _elapsed    = time.time() - _task_start

        if getattr(self, "_tg_mode", False):
            self._tg.send(f"📋 <b>Result:</b>\n<pre>{response[:3000]}</pre>")
        else:
            self._print_response(response)

        self.memory.add_message("user",      user_input, self.model)
        self.memory.add_message("assistant", response,   self.model)

        if not getattr(self, "_tg_mode", False):
            self._tg.notify_task_done(user_input, response, _elapsed)

    def run(self):
        self._print_banner()

        # Natural-language slash-aliases — typing these bare words still routes
        # to their /command handler (e.g. "shell" -> "/shell"), since that's a
        # deliberate command invocation, not a chat message.
        _NAT_CMDS = {"recon","note","notes","save","dryrun","target","shell","auth","mcp","config"}

        # ── Paste collapsing (Claude-Code-style) ─────────────────────
        # A large/multi-line paste is shown as a short placeholder like
        # "[Pasted text: 42 lines, 310 words #1]" instead of dumping the
        # whole blob into the prompt line. Backspacing right after the
        # placeholder deletes the whole thing in one step (and forgets the
        # underlying text). Placeholders are expanded back to the real text
        # right before the line is handed off to command/chat processing.
        _PASTE_LINE_THRESHOLD = 4          # collapse if pasted text has > N lines...
        _PASTE_CHAR_THRESHOLD = 400        # ...or is longer than N chars
        _paste_store: dict = {}
        _paste_counter = {"n": 0}
        _PASTE_RE = re.compile(r"\[Pasted text: \d+ lines?, \d+ words? #(\d+)\]")

        def _make_placeholder(text: str) -> str:
            _paste_counter["n"] += 1
            pid = _paste_counter["n"]
            lines = text.count("\n") + 1
            words = len(text.split())
            placeholder = (
                f"[Pasted text: {lines} line{'s' if lines != 1 else ''}, "
                f"{words} word{'s' if words != 1 else ''} #{pid}]"
            )
            _paste_store[placeholder] = text
            return placeholder

        def _expand_pastes(s: str) -> str:
            for placeholder, text in _paste_store.items():
                s = s.replace(placeholder, text)
            return s

        # ── prompt_toolkit session (rich completions + annotations) ──
        _pt_session = None
        if _PT_OK and _HackersCompleter is not None:
            from prompt_toolkit.key_binding import KeyBindings as _PTKeyBindings
            from prompt_toolkit.filters import completion_is_selected as _pt_sel
            from prompt_toolkit.keys import Keys as _PTKeys

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

            @_kb.add(_PTKeys.BracketedPaste)
            def _handle_bracketed_paste(event):
                """Collapse a real terminal paste into a short placeholder
                instead of inserting the raw pasted text."""
                data = event.data or ""
                buf = event.app.current_buffer
                if (data.count("\n") + 1) > _PASTE_LINE_THRESHOLD or len(data) > _PASTE_CHAR_THRESHOLD:
                    buf.insert_text(_make_placeholder(data))
                else:
                    buf.insert_text(data)

            @_kb.add("backspace")
            def _handle_backspace(event):
                """Backspace right after a paste placeholder deletes the
                whole placeholder (and the pasted text it stands for) in
                one step, instead of eating it character by character."""
                buf = event.app.current_buffer
                before = buf.document.text_before_cursor
                m = _PASTE_RE.search(before)
                if m and m.end() == len(before):
                    placeholder = m.group(0)
                    buf.delete_before_cursor(count=len(placeholder))
                    _paste_store.pop(placeholder, None)
                else:
                    buf.delete_before_cursor(count=1)

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

            # Expand any paste placeholders back into their real text now
            # that we know the full line — everything downstream (slash
            # commands, /shell aliasing, chat processing) sees the real
            # pasted content, only the on-screen prompt line stayed short.
            user_input = _expand_pastes(user_input)

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

            # ── Chat-only input ─────────────────────────────────────
            # Nothing typed at the bare prompt is ever executed directly on
            # the system anymore, no matter what it looks like (pipes,
            # redirects, "sudo ...", a bare binary name, etc). Everything
            # goes to the chat/model pipeline. The ONLY way to get a real
            # interactive system shell is the explicit "/shell" command
            # handled above (and by _NAT_CMDS -> "/shell").
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
