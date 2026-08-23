#!/usr/bin/env node
import React from "react";
import { render } from "ink";
import chalk from "chalk";
import App from "./ui/App.js";
import { loadConfig } from "./core/config.js";
import { ProxyClient } from "./core/proxyClient.js";

async function main() {
  const config = loadConfig();

  console.log(
    chalk.cyan.bold(`
  ╭──────────────────────────────────────────╮
  │   Hackers AI · terminal agent             │
  ╰──────────────────────────────────────────╯`)
  );
  console.log(chalk.dim(`  model  : ${config.model}`));
  console.log(chalk.dim(`  proxy  : ${config.proxyBaseUrl}`));
  console.log(chalk.dim(`  cwd    : ${config.cwd}\n`));

  const proxy = new ProxyClient(config.proxyBaseUrl, config.model);
  const ok = await proxy.health();
  if (!ok) {
    console.log(
      chalk.yellow(
        `  [!] Could not reach the proxy at ${config.proxyBaseUrl}.\n` +
          `      Start your local server (e.g. \`python server.py\`) first,\n` +
          `      or point elsewhere with HACKERS_AI_PROXY=<url> / \`/proxy <url>\`.\n`
      )
    );
  } else {
    console.log(chalk.green(`  [✓] Proxy reachable\n`));
  }

  render(<App config={config} />);
}

main();
