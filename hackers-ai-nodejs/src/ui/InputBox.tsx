import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

interface Props {
  onSubmit: (value: string) => void;
  history: string[];
  disabled?: boolean;
  placeholder?: string;
  promptLabel?: string;
}

/** A boxed, multi-line-capable prompt: Enter submits, Ctrl+J inserts a
 * newline, Up/Down walk submission history when the buffer is empty. */
export default function InputBox({
  onSubmit,
  history,
  disabled,
  placeholder = "Type a message, or /help for commands…",
  promptLabel = "›",
}: Props) {
  const [value, setValue] = useState("");
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);

  useInput(
    (input, key) => {
      if (disabled) return;

      if (key.return) {
        if (key.shift || key.meta) {
          setValue((v) => v + "\n");
          return;
        }
        const trimmed = value.trim();
        if (trimmed) onSubmit(trimmed);
        setValue("");
        setHistoryIndex(null);
        return;
      }

      if (key.ctrl && input === "j") {
        setValue((v) => v + "\n");
        return;
      }

      if (key.upArrow && value === "") {
        if (history.length === 0) return;
        const idx = historyIndex === null ? history.length - 1 : Math.max(0, historyIndex - 1);
        setHistoryIndex(idx);
        setValue(history[idx]);
        return;
      }

      if (key.downArrow && historyIndex !== null) {
        const idx = historyIndex + 1;
        if (idx >= history.length) {
          setHistoryIndex(null);
          setValue("");
        } else {
          setHistoryIndex(idx);
          setValue(history[idx]);
        }
        return;
      }

      if (key.backspace || key.delete) {
        setValue((v) => v.slice(0, -1));
        return;
      }

      if (key.ctrl && input === "u") {
        setValue("");
        return;
      }

      if (!key.ctrl && !key.meta && input) {
        setValue((v) => v + input);
      }
    },
    { isActive: !disabled }
  );

  const lines = value.length ? value.split("\n") : [""];

  return (
    <Box
      borderStyle="round"
      borderColor={disabled ? "gray" : "cyan"}
      paddingX={1}
      flexDirection="column"
    >
      {lines.map((line, i) => (
        <Box key={i}>
          <Text color={disabled ? "gray" : "cyan"} bold>
            {i === 0 ? `${promptLabel} ` : "  "}
          </Text>
          <Text dimColor={!line && value === ""}>
            {line || (i === 0 ? placeholder : "")}
          </Text>
          {i === lines.length - 1 && !disabled ? <Text color="cyan">▏</Text> : null}
        </Box>
      ))}
    </Box>
  );
}
