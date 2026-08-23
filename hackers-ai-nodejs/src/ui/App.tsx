import React, { useCallback, useMemo, useRef, useState } from "react";
import { randomUUID } from "node:crypto";
import { Box, Text, Static, useApp, useInput, useStdout } from "ink";
import Spinner from "ink-spinner";
import InputBox from "./InputBox.js";
import EventView from "./Message.js";
import { Agent } from "../core/agent.js";
import { ProxyClient } from "../core/proxyClient.js";
import type { AgentConfig, TranscriptEvent } from "../types.js";
import { setConfigValue } from "../core/config.js";

type Mode =
  | { kind: "idle" }
  | { kind: "busy"; label: string }
  | { kind: "confirm"; command: string; resolve: (ok: boolean) => void }
  | {
      kind: "ask";
      question: string;
      options?: string[];
      resolve: (answer: string) => void;
      draft: string;
    };

export default function App({ config }: { config: AgentConfig }) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const [events, setEvents] = useState<TranscriptEvent[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const proxy = useMemo(() => new ProxyClient(config.proxyBaseUrl, config.model), [config]);
  const agentRef = useRef<Agent | null>(null);

  const emit = useCallback((event: TranscriptEvent) => {
    setEvents((prev) => [...prev, event]);
  }, []);

  const confirmShell = useCallback(
    (command: string) =>
      new Promise<boolean>((resolve) => {
        setMode({ kind: "confirm", command, resolve });
      }),
    []
  );

  const askUser = useCallback(
    (question: string, options?: string[]) =>
      new Promise<string>((resolve) => {
        setMode({ kind: "ask", question, options, resolve, draft: "" });
      }),
    []
  );

  const notify = useCallback(() => {
    stdout?.write("\u0007"); // terminal bell
  }, [stdout]);

  if (!agentRef.current) {
    agentRef.current = new Agent({ proxy, cwd: config.cwd, emit, confirmShell, askUser, notify });
  }

  const runSlash = useCallback(
    (cmd: string) => {
      const [name, ...rest] = cmd.slice(1).split(" ");
      const arg = rest.join(" ").trim();
      switch (name) {
        case "help":
          emit({
            kind: "system",
            id: randomUUID(),
            text:
              "/help              show this message\n" +
              "/clear             clear the transcript\n" +
              "/model <name>      switch model (persisted)\n" +
              "/proxy <url>       switch proxy base URL (persisted)\n" +
              "/exit              quit",
          });
          break;
        case "clear":
          setEvents([]);
          agentRef.current?.reset();
          break;
        case "model":
          if (arg) {
            setConfigValue("model", arg);
            emit({ kind: "system", id: randomUUID(), text: `Model set to ${arg} (restart to apply).` });
          }
          break;
        case "proxy":
          if (arg) {
            setConfigValue("proxyBaseUrl", arg);
            emit({ kind: "system", id: randomUUID(), text: `Proxy set to ${arg} (restart to apply).` });
          }
          break;
        case "exit":
        case "quit":
          exit();
          break;
        default:
          emit({ kind: "system", id: randomUUID(), text: `Unknown command: /${name}` });
      }
    },
    [emit, exit]
  );

  const handleSubmit = useCallback(
    async (text: string) => {
      setHistory((h) => [...h, text]);
      if (text.startsWith("/")) {
        runSlash(text);
        return;
      }
      setMode({ kind: "busy", label: "Thinking" });
      try {
        await agentRef.current!.send(text);
      } catch (e: any) {
        emit({ kind: "system", id: randomUUID(), text: `Error: ${e?.message ?? e}` });
      } finally {
        setMode({ kind: "idle" });
      }
    },
    [emit, runSlash]
  );

  // ── modal key handling ──────────────────────────────────────
  useInput(
    (input, key) => {
      if (mode.kind === "confirm") {
        if (input.toLowerCase() === "y" || key.return) {
          mode.resolve(true);
          setMode({ kind: "busy", label: "Running" });
        } else if (input.toLowerCase() === "n" || key.escape) {
          mode.resolve(false);
          setMode({ kind: "busy", label: "Thinking" });
        }
      } else if (mode.kind === "ask" && !mode.options) {
        if (key.return) {
          mode.resolve(mode.draft);
          setMode({ kind: "busy", label: "Thinking" });
        } else if (key.backspace || key.delete) {
          setMode({ ...mode, draft: mode.draft.slice(0, -1) });
        } else if (!key.ctrl && !key.meta && input) {
          setMode({ ...mode, draft: mode.draft + input });
        }
      } else if (mode.kind === "ask" && mode.options) {
        const n = parseInt(input, 10);
        if (!isNaN(n) && n >= 1 && n <= mode.options.length) {
          mode.resolve(mode.options[n - 1]);
          setMode({ kind: "busy", label: "Thinking" });
        }
      }
    },
    { isActive: mode.kind === "confirm" || mode.kind === "ask" }
  );

  return (
    <Box flexDirection="column">
      <Static items={events}>
        {(event) => <EventView key={event.id} event={event} />}
      </Static>

      {mode.kind === "confirm" && (
        <Box marginTop={1} flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
          <Text color="yellow" bold>
            Run this command?
          </Text>
          <Text>{mode.command}</Text>
          <Text dimColor>[y] yes   [n] no</Text>
        </Box>
      )}

      {mode.kind === "ask" && (
        <Box marginTop={1} flexDirection="column" borderStyle="round" borderColor="blue" paddingX={1}>
          <Text color="blue" bold>
            {mode.question}
          </Text>
          {mode.options ? (
            mode.options.map((o, i) => (
              <Text key={i}>
                {"  "}[{i + 1}] {o}
              </Text>
            ))
          ) : (
            <Text>
              {"> "}
              {mode.draft}
              <Text color="blue">▏</Text>
            </Text>
          )}
        </Box>
      )}

      {mode.kind === "busy" && (
        <Box marginTop={1}>
          <Text color="magenta">
            <Spinner type="dots" /> {mode.label}…
          </Text>
        </Box>
      )}

      <Box marginTop={1}>
        <InputBox
          onSubmit={handleSubmit}
          history={history}
          disabled={mode.kind !== "idle"}
        />
      </Box>
    </Box>
  );
}
