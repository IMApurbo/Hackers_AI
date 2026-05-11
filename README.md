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
- [Commands Reference](#commands-reference)
- [Telegram Integration](#telegram-integration)
- [MCP Server Support](#mcp-server-support)
- [Authorized Pentesting Mode](#authorized-pentesting-mode)
- [Architecture](#architecture)
- [Legal Disclaimer](#legal-disclaimer)
- [License](#license)

---

## Overview

**Hackers AI** is a single-file, terminal-based AI agent built for Linux. It understands natural language, plans multi-step tasks, executes shell commands, and summarizes results — all from your terminal. It is designed for system administrators, developers, and authorized penetration testers who want an AI copilot that lives entirely on their machine.

It ships with a built-in **DeepSeek proxy server** (`deepseek_server.py`) that runs locally, giving you a completely free AI backend with no API key required.

---

## Features

- **Natural language → shell commands** — describe what you want; the agent plans and executes it
- **Multi-step task planner** — breaks complex goals into ordered steps with confirmation
- **Free AI backend** — run `deepseek_server.py` in a separate terminal for zero-cost inference
- **Swappable AI models** — plug in any OpenAI-compatible or Anthropic-compatible endpoint
- **Persistent memory** — SQLite-backed conversation history and task logs across sessions
- **Target & notes system** — track authorized targets and attach reconnaissance notes
- **MCP (Model Context Protocol) support** — connect external tool servers (Claude Desktop-style config)
- **Telegram bridge** — control the agent remotely via a Telegram bot with inline confirmation buttons
- **Dry-run mode** — preview execution plans without running anything
- **Session export** — save full session reports as Markdown
- **Recon suite** — built-in fast reconnaissance flow for authorized targets
- **Readline shell** — arrow-key history, tab completion, Ctrl+← / Ctrl+→ word movement
- **Authorized targets list** — gated pentesting: tools only activate on explicitly approved hosts

---

## Requirements

- Linux (Ubuntu, Kali, Debian, Arch, etc.)
- Python 3.8 or newer (standard library only — no pip installs needed for the main agent)
- Root / sudo access (the agent relaunches itself with `sudo` automatically)

For the free DeepSeek server (`deepseek_server.py`):

```bash
python deepseek_server.py 
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

Hackers AI includes `deepseek_server.py` — a local proxy that connects to DeepSeek's free inference tier and exposes it as an Anthropic-compatible `/v1/messages` endpoint. **This means you get AI inference for free, with no API key required.**

### Step 1 — Start the server in a separate terminal window

```bash
python3 deepseek_server.py
```

The server listens on `http://localhost:8765` by default. Leave this window open.

### Step 2 — Launch the agent in another terminal window

```bash
sudo python3 hackers-ai.py
```

The agent will automatically connect to the local server. That's it — no configuration needed.

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
python3 deepseek_server.py

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

---

## Commands Reference

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/clear` | Clear conversation history |
| `/history` | View recent conversation turns |
| `/profile` | Show host profile (IP, user, OS, tools) |
| `/tools` | List detected pentesting tools in PATH |
| `/sysinfo` | Full system information dump |
| `/switch <model>` | Switch AI model |
| `/exit` | Quit the agent |
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
| `/recon <domain>` | Run a fast recon sweep on a target |
| `/note <text>` | Save a note for the current target |
| `/notes [target]` | View saved notes |
| `/delnotes [target]` | Delete notes |
| **MCP** | |
| `/config` | Open MCP server config in your editor |
| `/mcp list` | List configured MCP servers |
| `/mcp use <name>` | Activate an MCP server |
| `/mcp stop` | Disconnect the active MCP server |
| **Notifications** | |
| `/telegram setup <token> <user_id>` | Connect a Telegram bot |
| `/telegram status` | Show Telegram bridge status |
| `/telegram disable` | Disable the Telegram bridge |

---

## Telegram Integration

Control the agent remotely from your phone or any Telegram client.

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Get your Telegram user ID (e.g. via [@userinfobot](https://t.me/userinfobot)).
3. Inside the agent:

```
/telegram setup <BOT_TOKEN> <YOUR_USER_ID>
```

### Remote usage

Once connected, send any message to your bot and the agent will execute it on the machine and stream the output back to you. Multi-step plans show inline **Yes / No** confirmation buttons so you can approve or cancel actions directly from Telegram.

Special bot commands:

| Command | Action |
|---|---|
| `/status` | Show agent status (host, IP, user, target, model) |
| `/help` | List all available commands |
| `/tg_stop` | Stop the Telegram bridge remotely |

---

## MCP Server Support

Hackers AI uses the [Model Context Protocol](https://modelcontextprotocol.io/) to connect external tool servers, using the same JSON config format as Claude Desktop.

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
/config           — open the config file in your editor
/mcp list         — list all configured servers
/mcp use my-tools — activate a server
/mcp stop         — disconnect
```

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

> **Only authorize systems you own or have explicit written permission to test.**

---

## Architecture

```
hackers-ai.py          ← single-file agent (no dependencies)
deepseek_server.py     ← local AI proxy server (run separately)
~/.hackers_ai.db       ← SQLite: history, notes, targets, MCP logs
~/.hackers_ai_mcp.json ← MCP server configuration
~/.hackers_ai_telegram.json  ← Telegram bot credentials
~/.hackers_ai_history  ← readline history
```

The agent communicates with any backend through the Anthropic `/v1/messages` protocol. `deepseek_server.py` bridges DeepSeek's free inference to this format, but any compatible proxy works.

---

## Legal Disclaimer

This tool is intended **for educational purposes and authorized security testing only**. You are solely responsible for ensuring you have proper written authorization before using any offensive features against any system. Unauthorized access to computer systems is illegal. The author assumes no liability for misuse.

---

## License

MIT License — © [IMApurbo](https://github.com/IMApurbo)
