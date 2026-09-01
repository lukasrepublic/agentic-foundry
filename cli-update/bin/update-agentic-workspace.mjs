#!/usr/bin/env node
// update-agentic-workspace — the npx entrypoint for bringing an installed workspace current
// (AC-UAW-1/-2). This file wires process.argv/stdio/env to the orchestrator and prints its output;
// EVERY behaviour lives in the shared `create-agentic-workspace` package, resolved at an EXACT
// version (AC-UAW-2) so this thin wrapper and the wizard entry point can never diverge into
// different behaviours. No file here vendors a copy of a module under create-agentic-workspace's
// own src/ — this file spawns no `claude` invocation itself; every one goes through
// runUpdate -> pluginRefresh.mjs's single, allowlisted (AC-UAW-14) spawn site.
//
// `claude`-invoking posture (AC-UAW-14, row d): unlike create-agentic-workspace, THIS entry point
// runs `claude` — the marketplace refresh and plugin update phases necessarily do. It never
// accepts the workspace trust dialog and never pre-grants anything: every invocation is drawn from
// one frozen, closed allowlist (ALLOWED_CLAUDE_SUBCOMMANDS) of non-interactive `plugin` subcommands,
// none of which can start a session or reach a trust dialog.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { runUpdate, parseUpdateArgv } from 'create-agentic-workspace/src/update.mjs';

const rawArgv = process.argv.slice(2);

// A parse failure here (an unknown flag, including a mistyped --cleanup) is reported the same way
// runUpdate reports any other refusal, before anything is spawned or written. runUpdate re-parses
// argv itself (parseUpdateArgv is pure and idempotent); this pre-check exists only so a malformed
// invocation fails fast with a clean message from the entry point, before pkgDir resolution below.
try {
  parseUpdateArgv(rawArgv);
} catch (e) {
  process.stderr.write(`refused: ${e.message}\n`);
  process.exit(1);
}

// `create-agentic-workspace`'s OWN package root, resolved via the dependency (AC-UAW-2) rather than
// a relative path — the templates/, permission-floor.json and package.json (for the `foundry` pins)
// Phase 3's reconcile needs all live there, one exact version away, never vendored here.
const pkgDir = path.dirname(
  fileURLToPath(import.meta.resolve('create-agentic-workspace/package.json')),
);

const { exitCode } = await runUpdate(rawArgv, {
  cwd: process.cwd(),
  configDir: process.env.CLAUDE_CONFIG_DIR || path.join(process.env.HOME || os.homedir(), '.claude'),
  homeDir: process.env.HOME || os.homedir(),
  pkgDir,
  output: process.stdout,
  spawnEnv: process.env,
});

process.exitCode = exitCode;
