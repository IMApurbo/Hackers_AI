# hackers-ai-cli

A Node/TypeScript, npm-installable terminal agent with a Claude-Code-style UI
(Ink): scrolling transcript, boxed multi-line input, tool-call cards, a
spinner, and inline y/n confirmation before any shell command runs.

It talks to the **same local proxy** your original Python script used —
an Anthropic-Messages-shaped HTTP server at `http://localhost:8765/v1/messages`
(override with `HACKERS_AI_PROXY=<url>` or `/proxy <url>` inside the app).
If you don't have that proxy running yet, this CLI will just print a warning
at startup and let you fix the URL — it won't crash.

## Install

```bash
cd hackers-ai-cli
npm install
npm run build
npm link          # makes `hackers-ai` available globally
```

Then from anywhere:

```bash
hackers-ai
```

Or without linking:

```bash
npm start
```

## Config

- `HACKERS_AI_PROXY` — proxy base URL (default `http://localhost:8765`)
- `HACKERS_AI_MODEL` — model name sent to the proxy (default `deepseek-chat`)
- Settings you change with `/model` or `/proxy` inside the app are saved to
  your OS config dir (via `conf`) and persist across restarts.

## Slash commands

- `/help` — list commands
- `/clear` — clear the transcript and reset the agent's context
- `/model <name>` — switch model (persisted, takes effect on restart)
- `/proxy <url>` — switch proxy URL (persisted, takes effect on restart)
- `/exit` — quit

## Keys

- `Enter` — submit
- `Ctrl+J` — insert a newline without submitting
- `Up` / `Down` — walk through previous inputs (when the input box is empty)
- `Ctrl+U` — clear the current input line

## What's implemented vs. what's carried over conceptually

This is a **full rewrite**, not a line-for-line port of the 5,000+ line
Python original — that file also contains a SQLite memory store, a Telegram
bot bridge, an MCP client, a user-profile "improve" mode, and a lot of
box-drawing terminal-width math that a proper Ink UI replaces outright.
What's here is the part you asked to rebuild — the Claude-Code-style agent
UI and its core loop — done properly in TypeScript:

- **Agent loop** (`src/core/agent.ts`) — same shape as the original's
  `ask_agentic`: send messages + tool schemas to the proxy, execute any
  `tool_use` blocks, feed `tool_result`s back, repeat until a plain text
  answer, capped at 30 turns.
- **Tools** (`src/core/tools.ts`) — `run_shell` (with an inline y/n confirm,
  like the original), `read_file`, `write_file`, `edit_file`, `grep`,
  `glob`, `web_fetch`, `ask_user`, `update_todos`, `notify_user` — same
  names/schemas as the original `TOOL_SCHEMAS`, so an existing proxy/system
  prompt tuned for those tool names keeps working unchanged.
- **UI** (`src/ui/`) — Ink-based: `Static` transcript so history never
  re-renders/flickers, a bordered multi-line `InputBox`, tool-call/result
  rendering, a todo/plan panel, and modal-style confirm/ask-user prompts.

Not carried over (flag if you want any of these rebuilt next, they're each
a separate, sizeable piece): the SQLite conversation memory + `/improve`
user-profile learning, the Telegram remote-control bridge, the MCP client,
and `run_subagent` (currently not wired to a nested agent instance).

## Note on environment

I wasn't able to run `npm install` / `tsc` in this sandbox (outbound
registry access is blocked here), so the build hasn't been compiled and
smoke-tested end-to-end. The code was written and reviewed carefully, but
please run `npm run build` on your machine and tell me about any type
errors or runtime issues — I'll fix them immediately.
