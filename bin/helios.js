#!/usr/bin/env node
/**
 * Helios npx/npm entrypoint.
 *
 *   npx github:satvikndxd/helios-ai-command-center            # install + demo
 *   npx github:satvikndxd/helios-ai-command-center tui        # any launcher cmd
 *
 * Node is only the bootstrapper: on first run it executes the bundled
 * install.sh (Python 3.11+ venv under ~/.helios, `helios` launcher on PATH,
 * synthetic demo seed), then forwards every invocation to the native
 * launcher. No Node code touches the runtime after that.
 */

"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

if (process.platform === "win32") {
  console.error("Helios supports macOS and Linux. On Windows, use WSL.");
  process.exit(1);
}

const heliosHome = process.env.HELIOS_HOME || path.join(os.homedir(), ".helios");
const launcher = path.join(heliosHome, "bin", "helios");
const packageRoot = path.join(__dirname, "..");
const installer = path.join(packageRoot, "install.sh");
const args = process.argv.slice(2);

function run(cmd, cmdArgs, env) {
  const result = spawnSync(cmd, cmdArgs, {
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  return result.status ?? 0;
}

const installed =
  fs.existsSync(launcher) &&
  fs.existsSync(path.join(heliosHome, "app", "src", "helios", "main.py"));

if (!installed) {
  console.log("◉ Helios is not installed yet — running the installer…\n");
  const env = { HELIOS_SOURCE_DIR: packageRoot };
  // If the user asked for a specific command, skip the demo during install
  // and run their command right after; bare `npx …` keeps the full
  // install→demo experience.
  if (args.length > 0) env.HELIOS_SKIP_DEMO = "1";
  const status = run("bash", [installer], env);
  if (status !== 0) process.exit(status);
  if (args.length === 0) process.exit(0);
}

process.exit(run(launcher, args.length ? args : ["help"]));
