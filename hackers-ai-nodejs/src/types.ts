export type Role = "user" | "assistant" | "tool";

export interface TextBlock {
  type: "text";
  text: string;
}

export interface ToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, any>;
}

export interface ToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string;
  is_error?: boolean;
}

export type ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock;

export interface Message {
  role: Role;
  content: ContentBlock[];
}

/** What gets rendered in the transcript — a flattened, UI-friendly event. */
export type TranscriptEvent =
  | { kind: "user"; text: string; id: string }
  | { kind: "assistant"; text: string; id: string }
  | { kind: "tool_call"; name: string; input: Record<string, any>; id: string }
  | {
      kind: "tool_result";
      name: string;
      output: string;
      isError: boolean;
      id: string;
    }
  | { kind: "system"; text: string; id: string }
  | { kind: "todos"; todos: Todo[]; id: string };

export interface Todo {
  text: string;
  status: "pending" | "in_progress" | "completed";
}

export interface AgentConfig {
  proxyBaseUrl: string;
  model: string;
  cwd: string;
}
