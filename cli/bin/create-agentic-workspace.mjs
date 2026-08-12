#!/usr/bin/env node
// create-agentic-workspace — the npx entrypoint (AC-BCL-2). Argv parsing itself lives in
// ../src/argv.mjs, derived from the one question table in ../src/questions.mjs; this file only
// wires process.argv/stdio to the orchestrator and prints its output.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { runCli } from '../src/run.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkgDir = path.resolve(__dirname, '..');

// A bare leading positional (`create-agentic-workspace my-app`) is sugar for `--dir my-app`
// (prior art: create-next-app/create-vite). It is translated here, before the table-derived
// argv parser ever sees it — the parser's own accepted-flag set stays exactly the question
// table (AC-BCL-2's bijection).
const rawArgv = process.argv.slice(2);
let argv = rawArgv;
if (rawArgv.length > 0 && !rawArgv[0].startsWith('--')) {
  argv = ['--dir', rawArgv[0], ...rawArgv.slice(1)];
}

// runCli streams every line to `output` as it is produced (ER #95), so the returned string is a
// transcript for callers, not something to print again here.
const { exitCode } = await runCli(argv, {
  cwd: process.cwd(),
  isTTY: Boolean(process.stdin.isTTY),
  input: process.stdin,
  output: process.stdout,
  homeDir: process.env.HOME || os.homedir(),
  pkgDir,
});

process.exitCode = exitCode;
