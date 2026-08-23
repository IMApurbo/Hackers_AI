import { exec } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import path from "node:path";
import fetch from "node-fetch";
import type { ProxyClient } from "./proxyClient.js";

const execAsync = promisify(exec);

/** Same tool-call surface as TOOL_SCHEMAS in the original Python script. */
export const TOOL_SCHEMAS = [
  {
    name: "run_shell",
    description:
      "Run a shell command and return its stdout/stderr/exit code. Never use " +
      "'cd' — each call is a fresh process; use absolute paths instead.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string", description: "The shell command to execute." },
      },
      required: ["command"],
    },
  },
  {
    name: "edit_file",
    description:
      "Make a surgical edit to an existing file, described in natural language. " +
      "Always prefer this over run_shell/write_file to modify an existing file.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the file to edit." },
        instruction: { type: "string", description: "Precise description of the change." },
      },
      required: ["path", "instruction"],
    },
  },
  {
    name: "read_file",
    description:
      "Read a file from disk with line numbers. Use offset/limit for large files.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string" },
        offset: { type: "integer", description: "1-based start line. Optional." },
        limit: { type: "integer", description: "Max lines to read. Default 2000." },
      },
      required: ["path"],
    },
  },
  {
    name: "write_file",
    description:
      "Create a new file or fully overwrite an existing one. Use edit_file to " +
      "change only part of a file.",
    input_schema: {
      type: "object",
      properties: {
        path: { type: "string" },
        content: { type: "string" },
      },
      required: ["path", "content"],
    },
  },
  {
    name: "grep",
    description: "Search file contents for a regex pattern across a directory or file.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string" },
        path: { type: "string", description: "Defaults to the current working directory." },
        glob: { type: "string", description: "Optional filename glob, e.g. '*.ts'." },
      },
      required: ["pattern"],
    },
  },
  {
    name: "glob",
    description: "Find files matching a name pattern, most recently modified first.",
    input_schema: {
      type: "object",
      properties: {
        pattern: { type: "string" },
        path: { type: "string" },
      },
      required: ["pattern"],
    },
  },
  {
    name: "web_fetch",
    description: "Fetch a URL and return its text content (HTML tags stripped).",
    input_schema: {
      type: "object",
      properties: { url: { type: "string" } },
      required: ["url"],
    },
  },
  {
    name: "ask_user",
    description:
      "Ask the user a clarifying question when genuinely blocked on a decision " +
      "only they can make.",
    input_schema: {
      type: "object",
      properties: {
        question: { type: "string" },
        options: { type: "array", items: { type: "string" } },
      },
      required: ["question"],
    },
  },
  {
    name: "update_todos",
    description:
      "Show the user your current step-by-step plan and progress. Pass the FULL " +
      "current list each call.",
    input_schema: {
      type: "object",
      properties: {
        todos: {
          type: "array",
          items: {
            type: "object",
            properties: {
              text: { type: "string" },
              status: { type: "string", enum: ["pending", "in_progress", "completed"] },
            },
            required: ["text", "status"],
          },
        },
      },
      required: ["todos"],
    },
  },
  {
    name: "notify_user",
    description: "Send a short notification to get the user's attention (e.g. a bell).",
    input_schema: {
      type: "object",
      properties: { message: { type: "string" } },
      required: ["message"],
    },
  },
] as const;

export interface ToolContext {
  cwd: string;
  proxy: ProxyClient;
  /** Resolve true/false for a shell command the model wants to run. */
  confirmShell: (command: string) => Promise<boolean>;
  /** Ask the human a free-text or multiple-choice question. */
  askUser: (question: string, options?: string[]) => Promise<string>;
  /** Push a todo-list update into the UI. */
  onTodos: (todos: { text: string; status: string }[]) => void;
  /** Ring the bell / surface a notification in the UI. */
  notify: (message: string) => void;
}

function resolvePath(cwd: string, p: string): string {
  return path.isAbsolute(p) ? p : path.resolve(cwd, p);
}

async function toolReadFile(ctx: ToolContext, input: any): Promise<string> {
  const full = resolvePath(ctx.cwd, input.path);
  const raw = await fs.readFile(full, "utf-8");
  const lines = raw.split("\n");
  const offset = Math.max(1, input.offset ?? 1);
  const limit = input.limit ?? 2000;
  const slice = lines.slice(offset - 1, offset - 1 + limit);
  return slice.map((l, i) => `${offset + i}\t${l}`).join("\n");
}

async function toolWriteFile(ctx: ToolContext, input: any): Promise<string> {
  const full = resolvePath(ctx.cwd, input.path);
  await fs.mkdir(path.dirname(full), { recursive: true });
  await fs.writeFile(full, input.content, "utf-8");
  return `Wrote ${input.content.length} bytes to ${full}`;
}

/** Surgical edit: sends the file + instruction back to the model and writes
 * the returned full contents. Simpler than the original's diff engine but
 * gives the same natural-language editing UX. */
async function toolEditFile(ctx: ToolContext, input: any): Promise<string> {
  const full = resolvePath(ctx.cwd, input.path);
  const original = await fs.readFile(full, "utf-8");
  const resp = await ctx.proxy.askAgentic(
    [
      {
        role: "user",
        content: [
          {
            type: "text",
            text:
              `Apply this instruction to the file and return ONLY the complete ` +
              `new file content, no commentary, no markdown fences.\n\n` +
              `Instruction: ${input.instruction}\n\n--- FILE (${input.path}) ---\n${original}`,
          },
        ],
      },
    ],
    "You are a precise code-editing engine. Output only the full resulting file.",
    []
  );
  const text = resp.content.find((b) => b.type === "text") as
    | { type: "text"; text: string }
    | undefined;
  if (!text) throw new Error("Model returned no content for edit_file");
  const cleaned = text.text.replace(/^```[a-z]*\n?/i, "").replace(/```\s*$/, "");
  await fs.writeFile(full, cleaned, "utf-8");
  return `Edited ${full} (${cleaned.length} bytes)`;
}

async function walk(dir: string, out: string[], depth = 0): Promise<void> {
  if (depth > 12) return;
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    if (e.name === "node_modules" || e.name === ".git") continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) await walk(full, out, depth + 1);
    else out.push(full);
  }
}

function globToRegExp(glob: string): RegExp {
  const escaped = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*/g, "\u0000")
    .replace(/\*/g, "[^/]*")
    .replace(/\u0000/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`${escaped}$`);
}

async function toolGrep(ctx: ToolContext, input: any): Promise<string> {
  const base = resolvePath(ctx.cwd, input.path || ".");
  const files: string[] = [];
  await walk(base, files);
  const globRe = input.glob ? globToRegExp(input.glob) : null;
  const patternRe = new RegExp(input.pattern);
  const matches: string[] = [];
  for (const f of files) {
    if (globRe && !globRe.test(path.basename(f))) continue;
    let content: string;
    try {
      content = await fs.readFile(f, "utf-8");
    } catch {
      continue;
    }
    const lines = content.split("\n");
    lines.forEach((l, i) => {
      if (patternRe.test(l)) matches.push(`${f}:${i + 1}:${l.trim()}`);
    });
    if (matches.length > 300) break;
  }
  return matches.length ? matches.slice(0, 300).join("\n") : "(no matches)";
}

async function toolGlob(ctx: ToolContext, input: any): Promise<string> {
  const base = resolvePath(ctx.cwd, input.path || ".");
  const files: string[] = [];
  await walk(base, files);
  const re = globToRegExp(input.pattern);
  const filtered: { f: string; mtime: number }[] = [];
  for (const f of files) {
    if (re.test(f.replace(base + path.sep, ""))) {
      const st = await fs.stat(f);
      filtered.push({ f, mtime: st.mtimeMs });
    }
  }
  filtered.sort((a, b) => b.mtime - a.mtime);
  return filtered.length ? filtered.map((x) => x.f).join("\n") : "(no matches)";
}

async function toolWebFetch(input: any): Promise<string> {
  const res = await fetch(input.url, { headers: { "User-Agent": "hackers-ai-cli" } });
  const raw = await res.text();
  const text = raw
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.slice(0, 8000);
}

export async function executeTool(
  ctx: ToolContext,
  name: string,
  input: Record<string, any>
): Promise<{ output: string; isError: boolean }> {
  try {
    switch (name) {
      case "run_shell": {
        const ok = await ctx.confirmShell(input.command);
        if (!ok) return { output: "User declined to run this command.", isError: true };
        const { stdout, stderr } = await execAsync(input.command, {
          cwd: ctx.cwd,
          maxBuffer: 10 * 1024 * 1024,
          shell: "/bin/bash",
        }).catch((e) => ({ stdout: e.stdout ?? "", stderr: e.stderr ?? String(e) }));
        const combined = [stdout, stderr].filter(Boolean).join("\n").trim();
        return { output: combined || "(no output)", isError: false };
      }
      case "read_file":
        return { output: await toolReadFile(ctx, input), isError: false };
      case "write_file":
        return { output: await toolWriteFile(ctx, input), isError: false };
      case "edit_file":
        return { output: await toolEditFile(ctx, input), isError: false };
      case "grep":
        return { output: await toolGrep(ctx, input), isError: false };
      case "glob":
        return { output: await toolGlob(ctx, input), isError: false };
      case "web_fetch":
        return { output: await toolWebFetch(input), isError: false };
      case "ask_user": {
        const answer = await ctx.askUser(input.question, input.options);
        return { output: answer, isError: false };
      }
      case "update_todos":
        ctx.onTodos(input.todos ?? []);
        return { output: "Todos updated.", isError: false };
      case "notify_user":
        ctx.notify(input.message ?? "");
        return { output: "Notified.", isError: false };
      default:
        return { output: `Unknown tool: ${name}`, isError: true };
    }
  } catch (e: any) {
    return { output: `Error: ${e?.message ?? String(e)}`, isError: true };
  }
}
