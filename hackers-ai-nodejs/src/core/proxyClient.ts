import fetch from "node-fetch";
import type { Message } from "../types.js";

export interface ProxyResponse {
  content: Array<
    | { type: "text"; text: string }
    | { type: "tool_use"; id: string; name: string; input: Record<string, any> }
  >;
  stop_reason?: string;
}

export class ProxyClient {
  constructor(private baseUrl: string, private model: string) {}

  async health(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/health`, { method: "GET" });
      return res.ok;
    } catch {
      return false;
    }
  }

  /** Multi-turn tool-calling call — mirrors FreeLLM.ask_agentic() from the
   * original script: same endpoint, same headers, same message shape. */
  async askAgentic(
    messages: Message[],
    system: string,
    tools: Record<string, any>[]
  ): Promise<ProxyResponse> {
    const res = await fetch(`${this.baseUrl}/v1/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": "local-proxy-key",
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: 4096,
        system,
        messages,
        tools,
      }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(
        `Proxy returned ${res.status} ${res.statusText} at ${this.baseUrl}/v1/messages\n${body}`
      );
    }

    return (await res.json()) as ProxyResponse;
  }
}
