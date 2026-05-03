#!/usr/bin/env python3
# issue is some command not show output like cp mv other stuff in linux , in that case it shows error when the command successfull let the ai generate "NO-OUT" for those command so its not wait for the output
"""
╔══════════════════════════════════════════════════════════════╗
║              HACKERS AI — Advanced Linux Agent               ║
║         General Purpose + Authorized Pentesting Suite        ║
║                   Single-File Architecture v7.0              ║
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
import urllib.request
import urllib.error

# ══════════════════════════════════════════════════════════════
# DEEPSEEK PROXY CLIENT  (replaces FreeLLM)
# Points at the local Anthropic-compatible proxy on port 8765.
# Start the proxy first:  python server.py [--headless]
# Override URL:  export HACKERS_AI_PROXY=http://localhost:8765
# ══════════════════════════════════════════════════════════════

PROXY_BASE_URL = os.environ.get("HACKERS_AI_PROXY", "http://localhost:8765")
PROXY_MODEL    = "deepseek-chat"   # proxy also accepts claude-* aliases

class FreeLLM:
    """
    Drop-in replacement for the original FreeLLM class.
    Sends requests to the local DeepSeek→Anthropic proxy server (server.py).
    The 'model' argument is forwarded as-is; the proxy translates
    claude-* names to deepseek-* automatically.
    """

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
MAX_HISTORY   = 10
MAX_RETRIES   = 3
DEFAULT_MODEL = PROXY_MODEL
VERSION       = "7.0.0"

# Targets that never need scope confirmation
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
# SECTION 2 — DATABASE
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
            conn.commit()

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
    """Enforces authorization confirmation for non-local targets."""

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
        """
        Returns (allowed: bool, reason: str, host: str)
        allowed=True  → proceed
        allowed=False → blocked, show reason
        allowed=None  → needs confirmation
        """
        # Only applies to pentest-like tasks
        if not self.PENTEST_VERBS.search(user_input):
            return True, "general task", ""

        # Find target in input or sticky
        targets = self.TARGET_RE.findall(user_input)
        if not targets and sticky_target:
            targets = self.TARGET_RE.findall(sticky_target)
        if not targets:
            return True, "no external target", ""

        host = self._extract_host(targets[0])

        # Local always allowed
        if self._is_local(host):
            return True, "local target", host

        # Check authorization DB
        if self.memory.is_authorized(host):
            return True, "authorized target", host

        # External, not authorized — needs confirmation
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
    """
    Fetches real --help output for tools silently.
    Used by planner to get exact flag syntax before generating commands.
    """

    # Tools where -h works better than --help
    PREFER_SHORT_HELP = {"nmap", "sqlmap", "hydra", "medusa", "dalfox", "xsstrike"}

    # Tools that need special invocation for help
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
            cmd = self.SPECIAL_HELP[tool]
            out = _quick_cmd(cmd, timeout=10)
        else:
            flags = ["-h", "--help"] if tool in self.PREFER_SHORT_HELP else ["--help", "-h"]
            out = ""
            for flag in flags:
                try:
                    r = subprocess.run(
                        [tool, flag], capture_output=True, text=True, timeout=6
                    )
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
                r = subprocess.run(
                    [tool, flag], capture_output=True, text=True, timeout=4
                )
                out = (r.stdout + r.stderr).strip()
                if out:
                    return out.splitlines()[0][:120]
            except Exception:
                continue
        return ""

    def identify_httpx(self) -> str:
        """
        Kali has two 'httpx' binaries:
          - httpx-toolkit (projectdiscovery scanner) — supports -title, -tech-detect etc.
          - httpx (encode HTTP client) — curl-like, different flags entirely
        Returns: 'scanner' | 'client' | 'none'
        """
        if shutil.which("httpx-toolkit"):
            return "scanner"
        if not shutil.which("httpx"):
            return "none"
        try:
            r = subprocess.run(
                ["httpx", "--version"], capture_output=True, text=True, timeout=4
            )
            out = (r.stdout + r.stderr).lower()
            if "projectdiscovery" in out or "httpx" in out and "next generation" not in out:
                # Check help for scanner-specific flags
                r2 = subprocess.run(
                    ["httpx", "-h"], capture_output=True, text=True, timeout=4
                )
                h = (r2.stdout + r2.stderr).lower()
                if "-title" in h or "-tech-detect" in h or "-status-code" in h:
                    return "scanner"
            return "client"
        except Exception:
            return "none"

class WordlistFinder:
    """Scans for available wordlists, returns only paths that exist."""

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
        """Returns dict of category → first existing path."""
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
            # Info gathering
            "nmap", "masscan", "rustscan", "naabu", "netdiscover",
            "subfinder", "amass", "assetfinder", "findomain", "sublist3r",
            "dnsenum", "dnsrecon", "fierce", "dnsx", "theharvester",
            "recon-ng", "shodan", "whois", "dig", "host", "nslookup",
            # Vuln analysis
            "nikto", "nuclei", "wpscan", "joomscan", "whatweb",
            "wafw00f", "sslscan", "sslyze", "testssl", "searchsploit",
            # Web
            "gobuster", "dirb", "dirsearch", "feroxbuster", "ffuf", "wfuzz",
            "katana", "httpx-toolkit", "arjun",
            "sqlmap", "commix", "dalfox", "xsstrike", "crlfuzz",
            "burpsuite", "zaproxy", "mitmproxy", "mitmdump",
            # Passwords
            "hashcat", "john", "hydra", "medusa", "ncrack",
            "cewl", "crunch", "cupp", "hash-identifier", "hashid",
            # Wireless
            "aircrack-ng", "airmon-ng", "airodump-ng", "aireplay-ng",
            "wifite", "kismet", "reaver",
            # Exploitation
            "msfconsole", "msfvenom", "pwncat", "evil-winrm",
            "crackmapexec", "netexec", "impacket-scripts",
            "rpcclient", "enum4linux", "enum4linux-ng",
            "smbclient", "smbmap", "ldapsearch",
            # Sniffing
            "wireshark", "tshark", "tcpdump", "netcat", "nc",
            "ettercap", "responder", "bettercap", "hping3",
            "fping", "arping",
            # Post-exploitation
            "linpeas", "winpeas", "linux-exploit-suggester", "pspy",
            "bloodhound", "neo4j",
            # Forensics/RE
            "binwalk", "strings", "file", "ltrace", "strace",
            "gdb", "radare2", "r2", "ghidra", "objdump", "readelf",
            "volatility3", "foremost", "exiftool", "steghide", "checksec",
            "jadx", "apktool",
            # OSINT
            "sherlock", "holehe", "maigret", "photon",
            # Cloud
            "trivy", "awscli", "az", "gcloud",
            # Networking
            "curl", "wget", "socat", "ncat",
            "ssh", "scp", "openssl", "gpg", "proxychains4", "tor",
            # Utils
            "python3", "python", "pip3",
            "ruby", "perl", "go", "gcc",
            "git", "docker", "tmux",
            "jq", "base64", "xxd",
        ]

        found = [t for t in pentest_tools if shutil.which(t)]

        # httpx disambiguation — add correct label
        inspector = ToolInspector()
        httpx_type = inspector.identify_httpx()
        if httpx_type == "scanner":
            binary = "httpx-toolkit" if shutil.which("httpx-toolkit") else "httpx"
            if binary not in found:
                found.append(binary)
        elif httpx_type == "client":
            # Rename to avoid confusion in prompts
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

        _print(c("dim", f"\n  ┌─ {tag}$ {command}"))
        stdout_lines = []
        stderr_lines = []
        start        = time.time()
        process      = None

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
                        _print(c("dim", f"  │ {tag}") + line)
                        stdout_lines.append(line)
            except KeyboardInterrupt:
                _print(c("yellow", f"\n  ├─ {tag}⚡ Ctrl+C — cancelling..."))
                _kill()
                elapsed = round(time.time() - start, 2)
                _print(c("yellow", f"  └─ {tag}✗ Cancelled ({elapsed}s)"))
                return {
                    "command": command, "stdout": "\n".join(stdout_lines),
                    "stderr": "Cancelled by user", "returncode": -2,
                    "success": False, "elapsed": elapsed, "cancelled": True,
                }

            process.stdout.close()
            process.wait(timeout=timeout)
            stderr_data = process.stderr.read()
            if stderr_data:
                stderr_lines = stderr_data.splitlines()

            elapsed = round(time.time() - start, 2)
            success = process.returncode == 0
            icon    = c("green", "✓") if success else c("red", "✗")
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

        # Fix markdown link corruption
        raw = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', raw)

        raw = raw.strip()

        # ── Extract code from fence ──
        fence_match = re.search(r'```[^\n]*\n(.*?)(?:```|$)', raw, re.DOTALL)
        if fence_match:
            # Check if fence line has content after ``` (e.g. ```#!/usr/bin/env python3)
            fence_line = re.search(r'```([^\n]+)\n', raw)
            if fence_line:
                tag = fence_line.group(1).strip()
                # If it's not a language tag, it's actual code (e.g. shebang)
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

        # ── Drop bare language tag lines ──
        cleaned_lines = []
        for line in raw.splitlines():
            if line.strip() in ("python", "python3", "py"):
                continue
            cleaned_lines.append(line)

        raw = "\n".join(cleaned_lines)

        if not raw.strip():
            return ""

        # ── Fix tabs → 4 spaces ──
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

    # If the text starts with {, treat it directly as JSON
    if text.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            pass

    # Otherwise extract from ```json ... ``` fence
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except Exception:
            text = fence_match.group(1).strip()

    # Strip any remaining fences and retry
    text = re.sub(r"```[a-zA-Z0-9]*\s*", "", text)
    text = re.sub(r"```", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    # Find first { and parse from there
    first = text.find("{")
    if first != -1:
        text = text[first:]
        try:
            return json.loads(text)
        except Exception:
            pass

        # Try fixing trailing commas
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", text))
        except Exception:
            pass

        # Last resort: close truncated JSON
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
        self.model   = model
        self.py_exec = py_exec
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
            agent = FreeLLM(model=self.model)
            raw   = agent.ask(prompt).strip()
            code  = PythonExecutor._clean_code(raw)
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
        parts   = failed_cmd.strip().split()
        tool    = parts[0] if parts else ""
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
# SECTION 12 — CONTEXT RESOLVER
# ══════════════════════════════════════════════════════════════

class ContextResolver:
    SYSTEM_CTX = textwrap.dedent("""
You are a context analysis module for a Linux agent.
Decide if the CURRENT TASK has everything needed to execute it.

RULES:
1. If task contains a domain, IP, or URL → ready=true
2. If task uses reference words ("the target", "that domain") → look in HISTORY for most recent domain/IP/URL. If found → ready=true, build enriched_task with value substituted.
3. LOCAL SYSTEM tasks (disk, memory, cpu, processes, files) → ready=true, no target needed.
4. If task clearly needs a target AND none found → ready=false, ask ONE short question.
5. NEVER ask if you already have the info. When in DOUBT → ready=true.

OUTPUT — respond ONLY with this exact JSON:
```json
{"ready":true,"found_in":"task","enriched_task":"<full task>","question":null}
```
OR:
```json
{"ready":false,"found_in":"none","enriched_task":null,"question":"<one short question>"}
```
    """).strip()

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._question_count = 0
        self._max_questions  = 2

    def resolve(self, user_input: str, history: list) -> dict:
        if self._question_count >= self._max_questions:
            self._question_count = 0
            return {"ready": True, "found_in": "fallback",
                    "enriched_task": user_input, "question": None}

        history_lines = []
        for h in reversed(history[-8:]):
            prefix  = "USER" if h["role"] == "user" else "AI"
            snippet = h["content"][:400].replace("\n", " ")
            history_lines.append(f"[{prefix}]: {snippet}")

        prompt = (
            self.SYSTEM_CTX + "\n\n"
            f"HISTORY (newest first):\n" + "\n".join(history_lines or ["(none)"]) + "\n\n"
            f"CURRENT TASK: {user_input}"
        )

        try:
            agent  = FreeLLM(model=self.model)
            raw    = agent.ask(prompt)
            result = extract_json(raw)
            if result and "ready" in result:
                if result.get("ready"):
                    self._question_count = 0
                    if not result.get("enriched_task"):
                        result["enriched_task"] = user_input
                else:
                    self._question_count += 1
                return result
        except Exception:
            pass

        return {"ready": True, "found_in": "fallback",
                "enriched_task": user_input, "question": None}

# ══════════════════════════════════════════════════════════════
# SECTION 13 — PLANNER ENGINE
# ══════════════════════════════════════════════════════════════

class PlannerEngine:
    # curl XSS payload progression — basic to advanced
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
        """
        Given completed step outputs, generate the next batch of steps.
        completed_steps: [{"id": 1, "desc": "...", "output": "..."}]
        """
        completed_str = "\n".join(
            f"Step {s['id']} — {s['desc']}:\n{s['output'][:800]}"
            for s in completed_steps
        )

        available_tools = profile.get("available_tools", [])
        relevant        = self._detect_relevant_tools(original_task, available_tools)
        tool_help       = self._fetch_tools_help(relevant)
        wordlists       = WordlistFinder().scan()
        system_ctx      = self._build_system_ctx(profile, tool_help, wordlists)
        system_ctx     += tool_help

        prompt = (
            system_ctx + "\n\n"
            f"ORIGINAL TASK: {original_task}\n\n"
            f"COMPLETED STEPS AND THEIR REAL OUTPUT:\n{completed_str}\n\n"
            "Based on the actual output above, generate ONLY the next required steps.\n"
            "Use the real output values directly in commands — do not re-run completed steps.\n"
            "If the task is fully complete, return an empty steps array: {\"steps\": []}\n"
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
                    filtered = []
                    for step in plan["steps"]:
                        desc_lower = (step.get("description") or "").lower()
                        FORBIDDEN_DESCS = [
                            "analyze result", "analyse result",
                            "summarize result", "summarise result",
                            "review result", "check result",
                            "generate report from result",
                        ]
                        if step.get("type") == "python" and any(
                            desc_lower == phrase or desc_lower.startswith(phrase)
                            for phrase in FORBIDDEN_DESCS
                        ):
                            continue
                        if step.get("type") == "python":
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
        """Silently fetch help for all relevant tools, return formatted string."""
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

        # ── Simple file ops need no tool help ────────────────────
        SIMPLE_OPS = [
            "delete", "remove", "rm ", "mkdir", "copy", "cp ", "move",
            "mv ", "rename", "touch", "chmod", "chown", "ln ", "cat ",
            "ls ", "list files", "list dir", "show files",
        ]
        if any(op in lower for op in SIMPLE_OPS):
            # Only fetch help if there's also a pentest/scan keyword
            PENTEST = ["scan", "xss", "sql", "inject", "fuzz", "brute",
                    "exploit", "recon", "enum", "crack", "hash"]
            if not any(p in lower for p in PENTEST):
                return []  # no tool help needed for plain file ops

        relevant = []

        # web tasks
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
            "exiftool":      ["exif", "metadata"],  # removed "image", "file" — too generic
        }

        for tool, keywords in tool_map.items():
            if tool in available and any(k in lower for k in keywords):
                relevant.append(tool)

        # Only add explicitly mentioned tools by exact word boundary match
        for t in available:
            if re.search(rf'\b{re.escape(t.lower())}\b', lower):
                relevant.append(t)

        return list(dict.fromkeys(relevant))

    def _build_system_ctx(self, profile: dict, tool_help: str, wordlists: dict) -> str:
        tools_str = ", ".join(profile.get("available_tools", [])) or "standard linux tools"

        # Format wordlists
        wl_finder = WordlistFinder()
        wordlist_str = wl_finder.format_for_prompt(wordlists)

        # httpx disambiguation
        inspector    = ToolInspector()
        httpx_type   = inspector.identify_httpx()
        httpx_note   = ""
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

        # Build curl XSS payload list for prompt
        payloads_str = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(self.CURL_XSS_PAYLOADS))

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
  Tools    : {tools_str}
{httpx_note}

{wordlist_str}

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
   IMPORTANT: If a later step needs to USE the output/result of a prior step
   (e.g. parse it, read a file it created, make decisions based on it),
   use type="python" for that step — shell commands cannot read prior stdout.
   Example: step 1 identifies hash type → step 2 must be python type to
   parse that output and choose the correct hashcat -m mode dynamically.
10. For wordlists: use ONLY paths from AVAILABLE WORDLISTS section above
    If no wordlist found for that category, omit the -w flag and note it.
11. ADAPTIVE steps: if a step's command depends on the RESULT of a previous
    step (not just a file), make it type="python" so it can subprocess the
    prior result and decide what to run. Never hardcode values that should
    come from prior step output.

═══ OUTPUT FORMAT — CRITICAL ═══
YOUR ENTIRE RESPONSE MUST BE EXACTLY THIS — NOTHING ELSE:

```json
{{{{
  "intent": "task",
  "summary": "<one-line summary>",
  "requires_root": false,
  "warning": null,
  "steps": [
    {{{{
      "id": 1,
      "type": "command",
      "tool": "<tool or null>",
      "command": "<exact shell command with single-quoted URLs>",
      "description": "<what this step does>",
      "depends_on": []
    }}}}
  ]
}}}}
```

MANDATORY:
- First characters of your response MUST be: ```json
- Last characters of your response MUST be: ```
- NO text before ```json — NO text after closing ```
- NO explanations, NO prose, NO comments outside the fence
- Outputting anything outside the fence BREAKS THE SYSTEM

type values: "command" | "python" | "info"
warning: null or string (not the string "null")
        """).strip()

    def plan(self, user_input: str, history: list, profile: dict,
             model: str = DEFAULT_MODEL) -> Optional[dict]:

        # ── Pre-flight: silently collect tool help + wordlists ──
        available_tools = profile.get("available_tools", [])
        relevant        = self._detect_relevant_tools(user_input, available_tools)

        print(c("dim", f"  [Planner] Fetching help for: {', '.join(relevant[:8])}..."), end="", flush=True)
        tool_help = self._fetch_tools_help(relevant)
        wordlists = WordlistFinder().scan()
        print(c("green", " done"))

        system_ctx = self._build_system_ctx(profile, tool_help, wordlists)
        system_ctx += tool_help  # append full tool help after main context

        # Build conversation context
        base_parts = [system_ctx, ""]
        for h in history[-2:]:
            prefix  = "USER" if h["role"] == "user" else "ASSISTANT"
            content = h["content"][:200] if h["role"] == "assistant" else h["content"]
            base_parts.append(f"[{prefix}]: {content}")

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

                plan = extract_json(raw)
                if plan and isinstance(plan.get("steps"), list) and plan["steps"]:
                    # Post-process: enforce python step rules
                    filtered = []
                    for step in plan["steps"]:
                        desc_lower = (step.get("description") or "").lower()
                        # Drop forbidden analysis-only steps — use exact phrases not partial matches
                        FORBIDDEN_DESCS = [
                            "analyze result", "analyse result",
                            "summarize result", "summarise result",
                            "review result", "check result",
                            "generate report from result",
                        ]
                        if step.get("type") == "python" and any(
                            desc_lower == phrase or desc_lower.startswith(phrase)
                            for phrase in FORBIDDEN_DESCS
                        ):
                            print(c("dim", f"  [Planner] Dropped forbidden python step: {desc_lower[:60]}"))
                            continue
                        if step.get("type") == "python":
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
- If execution needed, show exact command
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
        return response

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
        self._print_lock = threading.Lock()
        self._abort      = False
        self._cwd        = None
        self._user_input = ""

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
        steps      = [s for s in plan.get("steps", []) if s.get("type") != "info"]
        info_steps = [s for s in plan.get("steps", []) if s.get("type") == "info"]
        results    = {}
        step_stdout = {}  # ← store stdout of each step by id

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

                if cmd or stype == "python":
                    # ── Inject prior step outputs into python desc ──
                    if stype == "python" and step_stdout:
                        prior = "\n".join(
                            f"[Step {k} output]:\n{v}"
                            for k, v in step_stdout.items()
                            if v.strip()
                        )
                        if prior:
                            desc = f"{desc}\n\nPRIOR STEP OUTPUTS (use these directly, do not re-run commands to get them):\n{prior}"

                    result = self._run_with_healing(
                        cmd, stype, task_id, sid, step.get("tool",""), desc=desc
                    )
                    if result.get("cancelled"):
                        if not self._ask_continue():
                            self._abort = True
                            break

                    # ── Store stdout for next steps ──────────────
                    step_stdout[sid] = result.get("stdout", "").strip()

                    out = result["stdout"][:2500] or result["stderr"][:500]
                    results[sid] = f"[Step {sid} — {desc}]\n$ {result['command']}\n{out}"

            else:
                # parallel batch — unchanged
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
                    if not cmd and stype != "python":
                        return
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

        # ── Install fast-path ─────────────────────────────────
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

        # ── Pre-flight: check if the binary exists, install if not ──
        if stype != "python" and command.strip():
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
                    # immediately jump to alternative, skip retry loop
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
                        # fall through to normal retry loop with the alt command
                        command = alt_cmd

        # ── Phase 1: run + self-heal retries ──────────────────
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
                result = self.cmd_exec.run(
                    _cmd, label=label, lock=lock, cwd=effective_cwd
                )

            if result.get("cancelled"):
                return result

            self._log(task_id, step_id, tool, command,
                    result["stdout"], "success" if result["success"] else "error")

            if result["success"]:
                return result

            last_err = result["stderr"] or result["stdout"]

            # ── Python steps: just retry with error, skip shell healing ──
            if stype == "python":
                if attempt < MAX_RETRIES:
                    _lp(c("yellow", f"  ⚠ Python attempt {attempt}/{MAX_RETRIES} failed — regenerating..."))
                continue

            # ── Shell: skip retries if it's just command not found — already handled above ──
            if "command not found" in last_err.lower() or "exit:127" in last_err.lower():
                _lp(c("red", "  ✗ Binary still not found after install attempt — escalating."))
                break

            # ── Shell steps: permission bail ──────────────────
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

        # Soft-error check
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

        # Python exhausted all retries — stop here
        if stype == "python":
            _lp(c("red", f"  ✗ Step {step_id} — Python failed after {MAX_RETRIES} attempts."))
            return result

        _lp(c("red", "  ✗ Phase 1 exhausted — escalating..."))

        # ── Phase 2: install missing tool ─────────────────────
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

        # ── Phase 3: alternative command ──────────────────────
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

        # ── Phase 4: Python fallback ───────────────────────────
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

        self.engine._cwd        = self.engine._cwd
        self.engine._user_input = original_task
        completed   = []   # {"id", "desc", "output"}
        all_results = []
        step_offset = 0    # keep ids unique across rounds

        steps = initial_plan.get("steps", [])

        while True:
            if not steps:
                break

            # Split into independent and dependent
            independent = [s for s in steps if not s.get("depends_on")]
            dependent   = [s for s in steps if s.get("depends_on")]

            if not independent:
                # All remaining are dependent — run them with current outputs
                independent = steps
                dependent   = []

            print(c("cyan", f"\n  ⚡ Running {len(independent)} independent step(s)..."))

            # Execute independent steps
            mini_plan = {**initial_plan, "steps": independent}
            raw       = self.engine.execute_plan(mini_plan, task_id)
            all_results.append(raw)

            # Collect outputs
            for step in independent:
                sid  = step.get("id")
                desc = step.get("description", "")
                # Extract stdout from engine's internal log
                with sqlite3.connect(self.memory.db_path) as conn:
                    row = conn.execute(
                        "SELECT output FROM task_memory WHERE task_id=? AND step=? ORDER BY id DESC LIMIT 1",
                        (task_id, sid)
                    ).fetchone()
                output = row[0] if row else ""
                completed.append({"id": sid, "desc": desc, "output": output})

            if not dependent and not completed:
                break

            # Check if there are dependent steps remaining
            if not dependent:
                # Ask AI if task needs more steps based on output
                print(c("dim", "\n  [→] Checking if more steps needed..."))
                next_plan = self.planner.plan_next(
                    original_task, completed, profile, self.model
                )
                if not next_plan or not next_plan.get("steps"):
                    print(c("green", "  ✓ Task complete."))
                    break
                steps = next_plan["steps"]
            else:
                # Generate dependent steps with real output injected
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
- Plain text or minimal markdown. No headers for simple tasks.

Task: {original_request}
        """).strip()

        agent    = FreeLLM(model=self.model)
        response = agent.ask(system_ctx + f"\n\nOutput:\n{raw_results[:4000]}")
        return response

# ══════════════════════════════════════════════════════════════
# SECTION 17 — INTENT CLASSIFIER
# ══════════════════════════════════════════════════════════════

class IntentClassifier:
    SYSTEM_CTX = (
        "You are an intent classifier for a Linux AI agent. "
        "Decide if the user input requires EXECUTING something (task) "
        "or just ANSWERING a question (informational).\n\n"
        "TASK = anything that needs a shell command, opens an app, creates/modifies "
        "files, runs a scan, plays media, searches the web, installs software, "
        "or does anything on the system — even if phrased casually.\n\n"
        "INFORMATIONAL = pure questions about facts, concepts, or how things work "
        "with no system action needed.\n\n"
        "Examples:\n"
        "  'play ajhor brity on youtube'  → task\n"
        "  'scan 192.168.0.1'             → task\n"
        "  'open firefox'                 → task\n"
        "  'create a folder test'         → task\n"
        "  'what is nmap'                 → informational\n"
        "  'how does xss work'            → informational\n"
        "  'hello'                        → informational\n\n"
        "Reply with ONLY one word: task  or  informational"
    )

    @classmethod
    def classify(cls, text: str, model: str = DEFAULT_MODEL) -> str:
        try:
            agent  = FreeLLM(model=model)
            result = agent.ask(f"{cls.SYSTEM_CTX}\n\nUser input: {text}").strip().lower()
            if "task" in result:
                return "task"
            if "informational" in result:
                return "informational"
        except Exception:
            pass
        # fallback: assume task so nothing gets silently dropped
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

        # Stage 0: Tools
        print(c("yellow", "\n  ━━ Stage 0 — Tool Check ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        self._install_tools()

        # Stage 1: Subdomain enum
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

        # Stage 2: Live hosts
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

        # Build scan targets
        scan_targets = []
        if os.path.exists(live_file):
            with open(live_file) as lf:
                for _line in lf:
                    _url = _line.strip().split()[0].strip()
                    if _url.startswith("http") and _url not in scan_targets:
                        scan_targets.append(_url)
        if not scan_targets:
            scan_targets = [f"http://{domain}"]

        # Stage 3: WAF
        print(c("yellow", "\n  ━━ Stage 3 — WAF Detection ━━━━━━━━━━━━━━━━━━━━━━━━━"))
        waf_raw = ""
        if self._has("wafw00f"):
            for tgt in scan_targets[:3]:
                waf_raw += self._run(f"wafw00f {shlex.quote(tgt)} 2>/dev/null", "wafw00f", 60)
        report_parts += ["## WAF Detection", "```", waf_raw[:1500] or "none", "```", ""]

        # Stage 4: Port scan
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

        # Stage 5: Directory brute
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

        # Stage 6: Web tech
        print(c("yellow", "\n  ━━ Stage 6 — Web Tech Fingerprint ━━━━━━━━━━━━━━━━━━"))
        tech_raw = ""
        if self._has("whatweb"):
            for tgt in scan_targets[:3]:
                tech_raw += self._run(
                    f"whatweb -a 3 --no-errors {shlex.quote(tgt)} 2>/dev/null", "whatweb", 60
                )
        report_parts += ["## Web Technologies", "```", tech_raw[:2000] or "none", "```", ""]

        # Stage 7: Nikto
        print(c("yellow", "\n  ━━ Stage 7 — Nikto Scan ━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
        nikto_raw = ""
        if self._has("nikto"):
            for tgt in scan_targets[:2]:
                nikto_raw += self._run(
                    f"nikto -h {shlex.quote(tgt)} -nointeractive 2>/dev/null", "nikto", 300
                )
        report_parts += ["## Nikto Findings", "```", nikto_raw[:4000] or "none", "```", ""]

        # Stage 8: Nuclei
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

        # Stage 9: AI summary
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

        _sudo_user = os.environ.get("SUDO_USER")
        self.cwd = os.path.expanduser(f"~{_sudo_user}") if _sudo_user else os.getcwd()

        # Verify proxy is reachable before loading full system profile
        self._check_proxy()

        print(c("dim", "  [*] Profiling system..."), end="", flush=True)
        self.profile = self.profiler.profile()
        print(c("green", " done"))

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

    def _check_proxy(self):
        """Warn if the DeepSeek proxy server is not reachable."""
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
            print(c("yellow",
                f"\n  [!] WARNING: Proxy not reachable at {PROXY_BASE_URL}"))
            print(c("yellow",
                "  [!] Start it first:  python server.py [--headless]"))
            print(c("yellow",
                "  [!] Or set env var:  export HACKERS_AI_PROXY=http://host:port\n"))

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
            display = self.cwd
            _sudo_user = "root"
            _hostname  = "kali"

        target_str = ""
        if self.sticky_target:
            target_str = c("dim", f"[{self.sticky_target}]") + "\n"

        user_col = "red" if not self.run_as_user else "green"
        return (
            target_str +
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
                # Scope check for recon
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

        if slug == "/exit":
            print(c("cyan", "\n  Goodbye.\n"))
            sys.exit(0)

        return False

    def _confirm_scope(self, host: str) -> bool:
        """Ask user to confirm authorization for external target."""
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
        print(c("yellow", f"  ╠══ STEPS {'═'*53}"))
        for s in plan.get("steps", []):
            stype = s.get("type","command").upper()
            label = (s.get("command") or s.get("description") or "")[:82]
            tc    = "cyan" if stype == "COMMAND" else "magenta" if stype == "PYTHON" else "dim"
            print(f"  {w}  [{c(tc, f'{stype:<8}')}] {label}")
        print(c("yellow", f"  ╚{'═'*62}"))
        print()
        try:
            ans = input(c("cyan", "  Execute? [Y/n]: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        return ans in ("", "y", "yes")

    def _print_response(self, text: str):
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

    def process(self, user_input: str):
        self._inject_profile_context()

        # Enrich with sticky target if relevant
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

        # ── Scope check ───────────────────────────────────────
        allowed, reason, host = self.scope_guard.check(enriched_input, self.sticky_target)
        if allowed is None:
            # External target, needs confirmation
            if not self._confirm_scope(host):
                return
        # allowed=True → proceed, allowed=False → already blocked by confirm

        history = self.memory.get_history(MAX_HISTORY)
        intent  = IntentClassifier.classify(enriched_input)

        if intent == "informational":
            print(c("dim", "\n  [→] Informational query"))
            gen      = ResponseGenerator(model=self.model)
            response = gen.ask(enriched_input, history, self.profile)
            self._print_response(response)
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", response,   self.model)
            return

        # Context resolution
        print(c("dim", "\n  [→] Resolving context..."))
        resolver = ContextResolver(model=self.model)
        ctx      = resolver.resolve(enriched_input, history)

        if not ctx.get("ready", True):
            question = ctx.get("question", "Could you provide more details?")
            print()
            print(c("yellow", "  ╭─ Need more info " + "─"*44))
            print(c("yellow", f"  │  {question}"))
            print(c("yellow", "  ╰" + "─"*61))
            print()
            self.memory.add_message("user",      user_input, self.model)
            self.memory.add_message("assistant", f"[Awaiting: {question}]", self.model)
            return

        enriched = ctx.get("enriched_task") or enriched_input
        found_in = ctx.get("found_in", "task")
        if found_in == "history":
            print(c("cyan", f"  [✓] Target from history → {enriched[:80]}"))
        else:
            print(c("dim", "  [✓] Context resolved"))

        # Plan
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

        # Execute dynamically
        task_id     = datetime.now().strftime("%Y%m%d_%H%M%S")
        dyn_engine  = DynamicExecutionEngine(self.memory, model=self.model)
        dyn_engine.engine._cwd        = self.cwd
        dyn_engine.engine._user_input = enriched
        raw         = dyn_engine.run(enriched, plan, task_id, self.profile)

        # Summarize
        print(c("dim", "\n  [→] Summarizing..."))
        summary = Summarizer(model=self.model).summarize(raw, user_input, history)
        self._print_response(summary)
        self.memory.add_message("user",      user_input, self.model)
        self.memory.add_message("assistant", summary,    self.model)

        # Next-step suggestions
        if len(enriched.split()) > 3:
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
            "tar","zip","unzip","gzip","gunzip",
            "nano","vim","vi","nvim","python3","python","node","php",
            "ruby","bash","sh","zsh","clear","reset","tee",
            "base64","xxd","hexdump","strings","objdump","readelf",
            "strace","ltrace","ldd","nm","diff","patch",
        }

        _NAT_CMDS = {"recon","note","notes","save","dryrun","target","shell","auth"}

        while True:
            try:
                user_input = input(self._get_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print(c("cyan", "\n\n  Goodbye.\n"))
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                if not self._handle_slash(user_input):
                    print(c("red", f"  Unknown command: {user_input}. Type /help."))
                continue

            _words = user_input.strip().split()
            _first = _words[0].lower() if _words else ""

            # Natural slash-commands
            if _first in _NAT_CMDS:
                self._handle_slash("/" + user_input.strip())
                continue

            # cd
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

            # Direct shell fast-path
            _is_direct = (
                _first in _DIRECT_CMDS
                or user_input.strip().startswith("./")
                or "|" in user_input
                or ">" in user_input
                or "<" in user_input
                or user_input.strip().startswith("sudo ")
            ) and not any(
                kw in user_input.lower() for kw in
                ["install","download","create","make","build","setup",
                 "how","what","why","explain","describe","tell me",
                "and give","and show","and print","and tell",
                "server","service","daemon","host a","start a",
                "setup a","run a","launch a",]
            ) and len(user_input.split()) <= 6  # natural language is usually longer

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

            # AI task flow
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

    # Bust stale bytecode cache
    _cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
    if os.path.isdir(_cache):
        shutil.rmtree(_cache, ignore_errors=True)

    cli = CLI()
    cli.run()
