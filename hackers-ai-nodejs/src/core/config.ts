import Conf from "conf";
import type { AgentConfig } from "../types.js";

// Persisted settings live in the OS config dir (e.g. ~/.config/hackers-ai-cli).
const store = new Conf<{ proxyBaseUrl: string; model: string }>({
  projectName: "hackers-ai-cli",
  defaults: {
    proxyBaseUrl: "http://localhost:8765",
    model: "deepseek-chat",
  },
});

export function loadConfig(): AgentConfig {
  return {
    // env var wins, then persisted config, then default — same precedence
    // the original Python script used for HACKERS_AI_PROXY.
    proxyBaseUrl: process.env.HACKERS_AI_PROXY || store.get("proxyBaseUrl"),
    model: process.env.HACKERS_AI_MODEL || store.get("model"),
    cwd: process.cwd(),
  };
}

export function setConfigValue(key: "proxyBaseUrl" | "model", value: string) {
  store.set(key, value);
}

export function getConfigPath(): string {
  return store.path;
}
