import React from "react";
import { Box, Text } from "ink";
import type { TranscriptEvent } from "../types.js";

function truncate(s: string, max = 600): string {
  return s.length > max ? s.slice(0, max) + `\n… (${s.length - max} more chars)` : s;
}

export default function EventView({ event }: { event: TranscriptEvent }) {
  switch (event.kind) {
    case "user":
      return (
        <Box marginTop={1} flexDirection="column">
          <Text color="green" bold>
            {"› "}
            {event.text}
          </Text>
        </Box>
      );

    case "assistant":
      return (
        <Box marginTop={1} flexDirection="column">
          <Text color="white">{event.text}</Text>
        </Box>
      );

    case "tool_call":
      return (
        <Box marginTop={1} flexDirection="column">
          <Text color="magenta">
            ⏺ <Text bold>{event.name}</Text>
            <Text dimColor>{"  " + summarizeInput(event.name, event.input)}</Text>
          </Text>
        </Box>
      );

    case "tool_result":
      return (
        <Box flexDirection="column" paddingLeft={2}>
          <Text color={event.isError ? "red" : "gray"}>
            {event.isError ? "✗ " : "⎿ "}
            {truncate(event.output.trim() || "(empty)")}
          </Text>
        </Box>
      );

    case "todos":
      return (
        <Box marginTop={1} flexDirection="column">
          <Text color="cyan" bold>
            Plan
          </Text>
          {event.todos.map((t, i) => (
            <Text key={i}>
              {t.status === "completed" ? "  ✓ " : t.status === "in_progress" ? "  ▸ " : "  ○ "}
              <Text dimColor={t.status === "pending"}>{t.text}</Text>
            </Text>
          ))}
        </Box>
      );

    case "system":
      return (
        <Box marginTop={1}>
          <Text color="yellow">{event.text}</Text>
        </Box>
      );

    default:
      return null;
  }
}

function summarizeInput(name: string, input: Record<string, any>): string {
  switch (name) {
    case "run_shell":
      return input.command;
    case "read_file":
      return input.path;
    case "write_file":
      return input.path;
    case "edit_file":
      return `${input.path} — ${input.instruction}`;
    case "grep":
      return `"${input.pattern}" in ${input.path || "."}`;
    case "glob":
      return input.pattern;
    case "web_fetch":
      return input.url;
    case "notify_user":
      return input.message;
    default:
      return JSON.stringify(input).slice(0, 100);
  }
}
