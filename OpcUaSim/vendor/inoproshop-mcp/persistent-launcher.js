#!/usr/bin/env node
"use strict";

// Adapter around the upstream bundle. The bundle normally launches a fresh
// InoProShop.exe for every tool call and closes the project at the end of every
// generated script. This module intercepts only those --runscript launches,
// forwards the generated scripts to one long-running IronPython host, and
// presents a ChildProcess-compatible facade back to the unmodified bundle.

const childProcess = require("child_process");
const crypto = require("crypto");
const events = require("events");
const fs = require("fs");
const os = require("os");
const path = require("path");
const stream = require("stream");

const realSpawn = childProcess.spawn;
const hostScript = path.join(__dirname, "persistent_host.py");
const upstreamBundle = path.join(__dirname, "bundle.min.js");
const runningAsLauncher = require.main === module;
const sessionDir = process.env.OPCUASIM_INO_SESSION_DIR ||
  (runningAsLauncher ? fs.mkdtempSync(path.join(os.tmpdir(), "opcuasim-ino-")) : "");
const queueDir = sessionDir ? path.join(sessionDir, "queue") : "";
const heartbeatFile = sessionDir ? path.join(sessionDir, "heartbeat") : "";
const stopFile = sessionDir ? path.join(sessionDir, "stop") : "";

if (runningAsLauncher) fs.mkdirSync(queueDir, { recursive: true });

let hostProcess = null;
const pending = new Set();

function transformScript(source) {
  // Upstream scripts close either `project` or `primary_project` after every
  // operation. The host supplies this helper and performs the real close when
  // PLC-Sim writes the session stop marker.
  return source.replace(
    /\b(primary_project|project)\.close\(\)/g,
    "_opcuasim_keep_project_open($1)",
  );
}

function extractResultPath(source) {
  const match = source.match(/_RESULT_FILE\s*=\s*r?["']([^"']+)["']/);
  return match ? match[1] : null;
}

class PersistentChild extends events.EventEmitter {
  constructor(resultPath) {
    super();
    this.stdout = new stream.PassThrough();
    this.stderr = new stream.PassThrough();
    this.stdin = new stream.PassThrough();
    this.pid = hostProcess ? hostProcess.pid : undefined;
    this.exitCode = null;
    this.signalCode = null;
    this.killed = false;
    this.resultPath = resultPath;
    this._watcher = setInterval(() => this._checkResult(), 50);
    this._watcher.unref();
    pending.add(this);
  }

  _checkResult() {
    if (!this.resultPath) return;
    try {
      const text = fs.readFileSync(this.resultPath, "utf8");
      if (text.includes("SCRIPT_SUCCESS") || text.includes("SCRIPT_ERROR")) {
        this._finish(0, null);
      }
    } catch (_) {
      // The result file appears only after InoProShop begins executing the job.
    }
  }

  _finish(code, signal) {
    if (this.exitCode !== null || this.signalCode !== null) return;
    clearInterval(this._watcher);
    pending.delete(this);
    this.exitCode = code;
    this.signalCode = signal;
    process.nextTick(() => this.emit("close", code, signal));
  }

  kill(signal = "SIGTERM") {
    this.killed = true;
    this._finish(0, signal);
    return true;
  }
}

function failPending(message) {
  for (const request of Array.from(pending)) {
    request.stderr.write(message + "\n");
    request._finish(1, null);
  }
}

function ensureHost(executable, requestedArgs, options) {
  if (hostProcess && hostProcess.exitCode === null && !hostProcess.killed) {
    return;
  }
  try { fs.unlinkSync(stopFile); } catch (_) {}

  const profileArg = requestedArgs.find((arg) => String(arg).startsWith("--profile="));
  // InoProShop SP11 returns an internal NullReferenceException when a project
  // is opened from a delayed long-running script under --noUI. Keep the normal
  // UI engine but hide its process window; the browser remains the controller.
  const hostArgs = [profileArg, `--runscript=${hostScript}`].filter(Boolean);
  const env = {
    ...(options.env || process.env),
    OPCUASIM_INO_SESSION_DIR: sessionDir,
  };
  hostProcess = realSpawn(executable, hostArgs, {
    ...options,
    env,
    windowsHide: true,
    shell: false,
  });
  process.stderr.write(`[persistent-mcp] InoProShop session started pid=${hostProcess.pid}\n`);
  hostProcess.stdout.on("data", (chunk) => process.stderr.write(chunk));
  hostProcess.stderr.on("data", (chunk) => process.stderr.write(chunk));
  hostProcess.on("error", (error) => {
    failPending(`Persistent InoProShop host error: ${error.message}`);
  });
  hostProcess.on("close", (code, signal) => {
    process.stderr.write(
      `[persistent-mcp] InoProShop session exited code=${code} signal=${signal || ""}\n`,
    );
    hostProcess = null;
    failPending(`Persistent InoProShop host exited with code ${code}`);
  });
}

function enqueueScript(scriptPath) {
  const original = fs.readFileSync(scriptPath, "utf8");
  const transformed = transformScript(original);
  const id = `${Date.now()}-${crypto.randomBytes(6).toString("hex")}`;
  const temporary = path.join(queueDir, `${id}.tmp`);
  const job = path.join(queueDir, `${id}.job.py`);
  fs.writeFileSync(temporary, transformed, "utf8");
  fs.renameSync(temporary, job);
  return extractResultPath(original);
}

function installSpawnAdapter() {
  childProcess.spawn = function adaptedSpawn(executable, args = [], options = {}) {
    const scriptArg = args.find((arg) => String(arg).startsWith("--runscript="));
    if (!scriptArg) return realSpawn(executable, args, options);

    try {
      ensureHost(executable, args, options);
      const scriptPath = String(scriptArg).slice("--runscript=".length);
      const resultPath = enqueueScript(scriptPath);
      return new PersistentChild(resultPath);
    } catch (error) {
      const failed = new PersistentChild(null);
      process.nextTick(() => failed.emit("error", error));
      return failed;
    }
  };
}

function heartbeat() {
  try {
    fs.writeFileSync(heartbeatFile, String(Date.now()), "utf8");
  } catch (error) {
    process.stderr.write(`[persistent-mcp] heartbeat failed: ${error.message}\n`);
  }
}

module.exports = { extractResultPath, transformScript };

if (runningAsLauncher) {
  heartbeat();
  const timer = setInterval(heartbeat, 1000);
  timer.unref();
  installSpawnAdapter();
  require(upstreamBundle);
}
