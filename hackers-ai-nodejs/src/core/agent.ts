import { randomUUID } from "node:crypto";
import { ProxyClient } from "./proxyClient.js";
import { TOOL_SCHEMAS, executeTool, ToolContext } from "./tools.js";
import type { Message, TranscriptEvent } from "../types.js";

const MAX_AGENT_TURNS = 30;

const SYSTEM_PROMPT = `You are Hackers AI, an autonomous terminal agent running on the
user's Linux machine with full shell access via the run_shell tool. Prefer the
dedicated tools (read_file, write_file, edit_file, grep, glob) over run_shell
equivalents for file work — they're more reliable. Use update_todos for any
multi-step task so the user can follow your plan. Only call ask_user when you
are genuinely blocked on a decision only the user can make. Be concise in your
final answers; the user is watching a terminal, not reading a report.`;

export interface AgentDeps {
  proxy: ProxyClient;
  cwd: string;
  emit: (event: TranscriptEvent) => void;
  confirmShell: (command: string) => Promise<boolean>;
  askUser: (question: string, options?: string[]) => Promise<string>;
  notify: (message: string) => void;
}

export class Agent {
  private messages: Message[] = [];

  constructor(private deps: AgentDeps) {}

  async send(userText: string): Promise<void> {
    this.deps.emit({ kind: "user", text: userText, id: randomUUID() });
    this.messages.push({ role: "user", content: [{ type: "text", text: userText }] });

    const ctx: ToolContext = {
      cwd: this.deps.cwd,
      proxy: this.deps.proxy,
      confirmShell: this.deps.confirmShell,
      askUser: this.deps.askUser,
      onTodos: (todos) =>
        this.deps.emit({ kind: "todos", todos: todos as any, id: randomUUID() }),
      notify: (m) => this.deps.notify(m),
    };

    for (let turn = 0; turn < MAX_AGENT_TURNS; turn++) {
      const resp = await this.deps.proxy.askAgentic(
        this.messages,
        SYSTEM_PROMPT,
        TOOL_SCHEMAS as unknown as Record<string, any>[]
      );

      const assistantContent = resp.content;
      this.messages.push({ role: "assistant", content: assistantContent as any });

      const toolCalls = assistantContent.filter((b) => b.type === "tool_use") as Extract<
        typeof assistantContent[number],
        { type: "tool_use" }
      >[];
      const textBlocks = assistantContent.filter((b) => b.type === "text") as Extract<
        typeof assistantContent[number],
        { type: "text" }
      >[];

      for (const t of textBlocks) {
        if (t.text.trim()) {
          this.deps.emit({ kind: "assistant", text: t.text, id: randomUUID() });
        }
      }

      if (toolCalls.length === 0) {
        return; // final answer for this turn — done
      }

      const toolResults: Message["content"] = [];
      for (const call of toolCalls) {
        this.deps.emit({
          kind: "tool_call",
          name: call.name,
          input: call.input,
          id: call.id,
        });
        const result = await executeTool(ctx, call.name, call.input);
        this.deps.emit({
          kind: "tool_result",
          name: call.name,
          output: result.output,
          isError: result.isError,
          id: call.id,
        });
        toolResults.push({
          type: "tool_result",
          tool_use_id: call.id,
          content: result.output,
          is_error: result.isError,
        });
      }
      this.messages.push({ role: "user", content: toolResults });
    }

    this.deps.emit({
      kind: "system",
      text: `Stopped after ${MAX_AGENT_TURNS} turns without a final answer.`,
      id: randomUUID(),
    });
  }

  reset(): void {
    this.messages = [];
  }
}
