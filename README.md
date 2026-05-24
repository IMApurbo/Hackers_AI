# Hackers AI

```
  ██╗  ██╗ █████╗  ██████╗██╗  ██╗███████╗██████╗ ███████╗     █████╗ ██╗
  ██║  ██║██╔══██╗██╔════╝██║ ██╔╝██╔════╝██╔══██╗██╔════╝    ██╔══██╗██║
  ███████║███████║██║     █████╔╝ █████╗  ██████╔╝███████╗    ███████║██║
  ██╔══██║██╔══██║██║     ██╔═██╗ ██╔══╝  ██╔══██╗╚════██║    ██╔══██║██║
  ██║  ██║██║  ██║╚██████╗██║  ██╗███████╗██║  ██║███████║    ██║  ██║██║
  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝    ╚═╝  ╚═╝╚═╝
         Advanced Linux Agent · General Purpose + Authorized Pentesting
```

> **Version 7.3.0** — Single-file AI agent for Linux power users and authorized penetration testers.
> Powered by DeepSeek (free, no API key required) or any compatible AI backend.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-green.svg)]()

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Free Server Setup (No API Key)](#free-server-setup-no-api-key)
- [Using Other AI Backends](#using-other-ai-backends)
- [Usage](#usage)
- [CLI Flags](#cli-flags)
- [Commands Reference](#commands-reference)
- [Telegram Integration](#telegram-integration)
- [MCP Server Support](#mcp-server-support)
- [User Profile & Memory](#user-profile--memory)
- [Authorized Pentesting Mode](#authorized-pentesting-mode)
- [Architecture](#architecture)
- [Legal Disclaimer](#legal-disclaimer)
- [License](#license)

---

## Overview

**Hackers AI** is a single-file, terminal-based AI agent built for Linux. It understands natural language, plans multi-step tasks, executes shell commands, and summarizes results — all from your terminal. It is designed for system administrators, developers, and authorized penetration testers who want an AI copilot that lives entirely on their machine.

It ships with a built-in **DeepSeek proxy server** (`server.py`) that runs locally, giving you a completely free AI backend with no API key required.

---

## Features

- **Natural language → shell commands** — describe what you want; the agent plans and executes it
- **Multi-step task planner** — breaks complex goals into ordered steps, with parallel batch execution for independent steps
- **Free AI backend** — run `server.py` in a separate terminal for zero-cost inference
- **Swappable AI models** — plug in any OpenAI-compatible or Anthropic-compatible endpoint
- **Persistent memory** — SQLite-backed conversation history and task logs across sessions
- **Session restore** — on startup, optionally resume the previous session's conversation history
- **User profile & learning** — builds a persistent profile of your preferences, tools, and habits from session history; auto-updates on exit
- **Target & notes system** — track authorized targets and attach reconnaissance notes
- **MCP (Model Context Protocol) support** — connect external tool servers (Claude Desktop-style config); last active server is auto-restored on startup
- **Telegram bridge** — control the agent remotely via a Telegram bot with inline Yes/No/Add confirmation buttons; auto-starts if previously configured
- **Dry-run mode** — preview execution plans without running anything
- **Session export** — save full session reports as Markdown
- **Recon suite** — 9-stage automated recon pipeline with AI assessment for authorized targets
- **prompt_toolkit shell** — rich tab completion with annotations, path completion, arrow-key history (gracefully falls back to readline if not installed)
- **Authorized targets list** — gated pentesting: offensive tools only activate on explicitly approved hosts
- **Smart shell detection** — distinguishes natural language instructions from direct shell commands automatically; handles `cd` natively
- **GUI command support** — automatically runs GUI apps (browsers, IDEs, Wireshark, etc.) as the real user even when running under sudo
- **Scope guard** — prompts for confirmation before running offensive tools against unrecognized targets

---

## Requirements

- Linux (Ubuntu, Kali, Debian, Arch, etc.)
- Python 3.8 or newer (standard library only — no pip installs needed for the core agent)
- Root / sudo access (the agent re-launches itself with `sudo` automatically)

**Optional (recommended) — enhanced shell experience:**

```bash
pip install prompt_toolkit --break-system-packages
```

Without `prompt_toolkit` the agent falls back to readline, which still provides arrow-key history and tab completion.

**For the free DeepSeek server (`server.py`)**, run it in a separate terminal:

```bash
python3 server.py
```

---

## Installation

```bash
# Clone the repo
git clone https://github.com/IMApurbo/hackers-ai.git
cd hackers-ai

# Make the main agent executable
chmod +x hackers-ai.py
```

No virtual environment or package installation is needed for the core agent — it uses only Python's standard library.

---

## Free Server Setup (No API Key)

Hackers AI includes `server.py` — a local proxy that connects to DeepSeek's free inference tier and exposes it as an Anthropic-compatible `/v1/messages` endpoint. **This means you get AI inference for free, with no API key required.**

### Step 1 — Start the server in a separate terminal window

```bash
python3 server.py
```

The server listens on `http://localhost:8765` by default. Leave this window open.

### Step 2 — Launch the agent in another terminal window

```bash
sudo python3 hackers-ai.py
```

The agent automatically connects to the local server. No additional configuration needed.

### Custom server address

If you run the server on a different host or port, point the agent to it:

```bash
export HACKERS_AI_PROXY="http://192.168.1.10:8765"
sudo python3 hackers-ai.py
```

---

## Using Other AI Backends

Hackers AI talks to any server that speaks the Anthropic `/v1/messages` protocol. You can swap the backend at any time.

### Switch model at runtime

```
/switch <model_name>
```

### OpenAI-compatible backends

Run a local bridge (e.g. [LiteLLM](https://github.com/BerriAI/litellm)) that translates OpenAI → Anthropic format, then point `HACKERS_AI_PROXY` at it:

```bash
export HACKERS_AI_PROXY="http://localhost:4000"
sudo python3 hackers-ai.py
```

### Ollama (local models)

Use a thin proxy or LiteLLM to expose your Ollama models on the Anthropic message format, then set `HACKERS_AI_PROXY` accordingly.

### Hosted APIs

Any API that supports the Anthropic messages format (Claude API, Together AI, Groq with a bridge, etc.) works. Set the proxy URL to the host endpoint and ensure your server forwards the correct auth headers.

---

## Usage

```bash
# Start the free server first (separate terminal)
python3 server.py

# Then launch the agent
sudo python3 hackers-ai.py
```

Once inside the agent, just type naturally:

```
> scan the current machine for open ports
> set up a python HTTP server in /tmp
> find all SUID binaries on this system
> what processes are listening on port 80?
> summarize the last 50 lines of /var/log/syslog
```

Direct shell commands are also recognized and passed straight to bash:

```
> ls -la /etc
> ps aux | grep nginx
> sudo systemctl status apache2
> cat /etc/passwd | grep root
```

`cd` is handled natively — your working directory persists across commands and is shown in the prompt.

---

## CLI Flags

| Flag | Description |
|---|---|
| `-i` / `--improve` | Start with user profile mode active: display the current learned profile on launch and update it from session history on exit |

```bash
sudo python3 hackers-ai.py --improve
```

---

## Commands Reference

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/clear` | Clear conversation history |
| `/history` | View recent conversation turns |
| `/profile` | Show host profile (IP, user, OS, tools) |
| `/profile memory` | Show learned user facts from the persistent profile |
| `/profile edit` | Open the learned profile file in your editor |
| `/tools` | List detected pentesting tools in PATH |
| `/sysinfo` | Full system information dump |
| `/switch <model>` | Switch AI model |
| `/exit` | Save memory + quit the agent |
| **Session** | |
| `/target <host>` | Set a sticky target for all commands |
| `/target clear` | Clear the sticky target |
| `/auth add <host>` | Authorize a host for pentesting |
| `/auth list` | List authorized targets |
| `/auth remove <host>` | Remove a target from the authorized list |
| `/shell` | Drop into a raw bash shell |
| `/save [name]` | Export session as a Markdown report |
| `/dryrun` | Toggle dry-run mode (plan without executing) |
| **Recon** | |
| `/recon <domain>` | Run a full 9-stage automated recon pipeline on a target |
| `/note <target> <text>` | Save a note for a target |
| `/notes [target]` | View saved notes |
| `/delnotes [target]` | Delete notes |
| **MCP** | |
| `/config` | Open MCP server config in your editor |
| `/mcp list` | List configured MCP servers |
| `/mcp use <name>` | Activate an MCP server |
| `/mcp stop` | Disconnect the active MCP server |
| `/mcp reload` | Reload MCP config from disk |
| `/mcp tools [name]` | List tools exposed by an MCP server |
| `/mcp call <tool> [args]` | Directly call an MCP tool with JSON args |
| `/mcp ai <task>` | Use the AI to plan and execute MCP tool calls for a task |
| **Memory** | |
| `/improve` | Scan session history and update the persistent user profile |
| **Notifications** | |
| `/telegram --api-token <token> --user-id <id>` | Connect a Telegram bot |
| `/telegram status` | Show Telegram bridge status |
| `/telegram start` | Start the Telegram polling loop |
| `/telegram stop` | Stop the Telegram polling loop |
| `/telegram test` | Send a test message to your bot |

---

## Telegram Integration

Control the agent remotely from your phone or any Telegram client.

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Get your Telegram user ID (e.g. via [@userinfobot](https://t.me/userinfobot)).
3. Inside the agent:

```
/telegram --api-token <BOT_TOKEN> --user-id <YOUR_USER_ID>
```

The bot auto-starts on subsequent launches if it was previously configured and enabled.

### Remote usage

Once connected, send any message to your bot and the agent will execute it on the machine and stream the output back to you. Multi-step plans show inline **Yes (once) / Yes + Add to list / No** confirmation buttons so you can approve or cancel actions directly from Telegram.

Special bot commands:

| Command | Action |
|---|---|
| `/status` | Show agent status (host, IP, user, target, model, dry-run state) |
| `/help` | List all available commands |
| `/tg_stop` | Stop the Telegram bridge remotely |

---

## MCP Server Support

Hackers AI uses the [Model Context Protocol](https://modelcontextprotocol.io/) to connect external tool servers, using the same JSON config format as Claude Desktop. The last active MCP server is automatically restored on each startup.

### Config file location

```
~/.hackers_ai_mcp.json
```

### Example config

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "/usr/bin/python3",
      "args": ["/path/to/my_mcp_server.py"],
      "env": {
        "SOME_VAR": "value"
      }
    }
  }
}
```

### Managing MCP servers

```
/config               — open the config file in your editor
/mcp list             — list all configured servers
/mcp use my-tools     — activate a server
/mcp stop             — disconnect
/mcp tools my-tools   — list tools a server exposes
/mcp call <tool> {}   — call a tool directly with JSON args
/mcp ai <task>        — let the AI plan and call tools for a task
```

---

## User Profile & Memory

Hackers AI builds a persistent, deduplicated profile of you across sessions — your preferred language, tools, skills, and habits — stored in `~/.hackers_ai_profile.txt`.

**How it works:**

- On every `/exit` the agent silently scans the session history for new facts about you and appends or merges them into the profile.
- The profile is injected into every AI prompt so responses are always personalised.
- Running with `--improve` (or `-i`) shows your current profile at startup and prints changes on exit.

**Commands:**

```
/improve              — manually trigger a profile update from session history
/profile memory       — view all learned facts
/profile edit         — open the profile file in your editor to add/remove facts
```

**Profile file:** `~/.hackers_ai_profile.txt` (one fact per line, owner-read-only `chmod 600`)

---

## Authorized Pentesting Mode

Hackers AI will refuse to run offensive tools (nmap, sqlmap, hydra, etc.) against targets that have not been explicitly authorized. This is a deliberate safety gate.

```bash
# Authorize a target before running any offensive commands against it
/auth add 192.168.1.100
/auth add example-lab.local

# Confirm the list
/auth list

# Set it as the sticky target for all subsequent commands
/target 192.168.1.100

# Now run recon — the agent knows this target is authorized
/recon 192.168.1.100
```

The `/recon` pipeline runs 9 automated stages: tool check & install, subdomain enumeration, live host filtering, WAF detection, port scanning, directory brute-force, web tech fingerprinting, vulnerability scanning (Nikto + Nuclei), and an AI-generated assessment with risk scoring.

> **Only authorize systems you own or have explicit written permission to test.**

---

## Architecture

```
hackers-ai.py               ← single-file agent (standard library only)
server.py                   ← local AI proxy server (run separately)
~/.hackers_ai.db            ← SQLite: history, notes, targets, MCP logs, profile log
~/.hackers_ai_mcp.json      ← MCP server configuration (Claude Desktop format)
~/.hackers_ai_profile.txt   ← persistent learned user profile (one fact per line)
~/.hackers_ai_telegram.json ← Telegram bot credentials
~/.hackers_ai_history       ← readline/prompt_toolkit input history
```

The agent communicates with any backend through the Anthropic `/v1/messages` protocol. `server.py` bridges DeepSeek's free inference to this format, but any compatible proxy works.

---

## Legal Disclaimer

This tool is intended **for educational purposes and authorized security testing only**. You are solely responsible for ensuring you have proper written authorization before using any offensive features against any system. Unauthorized access to computer systems is illegal. The author assumes no liability for misuse.

---

## License

MIT License — © [IMApurbo](https://github.com/IMApurbo)
