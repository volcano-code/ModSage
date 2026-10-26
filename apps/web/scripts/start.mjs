import { spawn } from "node:child_process";
import { constants } from "node:os";
import { cpSync, existsSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { isIP } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const standalone = path.join(projectRoot, ".next", "standalone");
const server = path.join(standalone, "server.js");

class StartupError extends Error {}

function usage() {
  console.log("ModSage 本地生产启动：npm run start -- [--port 3000] [--hostname 127.0.0.1]");
  console.log("先执行 npm run build；默认只监听本机。Docker 直接运行镜像内的 server.js。");
}

function options(args) {
  const result = { port: "3000", hostname: "127.0.0.1" };
  const seen = new Set();
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--help" || argument === "-h") {
      if (args.length !== 1) throw new StartupError("--help 请单独使用。");
      return null;
    }
    const equals = argument.indexOf("=");
    const name = equals === -1 ? argument : argument.slice(0, equals);
    const key = { "--port": "port", "-p": "port", "--hostname": "hostname", "-H": "hostname" }[name];
    if (!key || seen.has(key)) throw new StartupError("参数未知或重复；使用 --help 查看支持的参数。");
    const value = equals === -1 ? args[++index] : argument.slice(equals + 1);
    if (!value || value.startsWith("-")) throw new StartupError("启动参数缺少值；使用 --help 查看用法。");
    seen.add(key);
    result[key] = value;
  }
  if (!/^\d+$/.test(result.port) || Number(result.port) < 1 || Number(result.port) > 65535) {
    throw new StartupError("端口必须是 1 到 65535 之间的整数。");
  }
  if (result.hostname !== "localhost" && !isIP(result.hostname)) {
    throw new StartupError("监听地址须为 IP 地址或 localhost；本地部署建议使用 127.0.0.1。");
  }
  result.port = String(Number(result.port));
  return result;
}

function prepareBuild() {
  const buildId = path.join(projectRoot, ".next", "BUILD_ID");
  const standaloneId = path.join(standalone, ".next", "BUILD_ID");
  const staticFiles = path.join(projectRoot, ".next", "static");
  const publicFiles = path.join(projectRoot, "public");
  if (![server, buildId, standaloneId, staticFiles, publicFiles].every(existsSync)) {
    throw new StartupError("未找到完整生产构建；请先在 apps/web 执行 npm run build。");
  }
  if (
    !statSync(server).isFile() ||
    !statSync(staticFiles).isDirectory() ||
    !statSync(publicFiles).isDirectory() ||
    readFileSync(buildId, "utf8") !== readFileSync(standaloneId, "utf8")
  ) {
    throw new StartupError("生产构建不完整或版本不一致；请重新执行 npm run build。");
  }
  // Next standalone excludes these assets, just as the Dockerfile copies them.
  // Copy only static/public assets, never .env files or application source folders.
  cpSync(staticFiles, path.join(standalone, ".next", "static"), { recursive: true });
  cpSync(publicFiles, path.join(standalone, "public"), { recursive: true });
}

function start(configuration) {
  prepareBuild();
  const require = createRequire(import.meta.url);
  const { loadEnvConfig } = require("@next/env");
  let environmentError = false;
  process.env.NODE_ENV = "production";
  // Keep Next's .env.local semantics; already-set API_BASE_URL takes precedence.
  loadEnvConfig(projectRoot, false, {
    info() {},
    error() { environmentError = true; },
  });
  if (environmentError) throw new StartupError("无法加载前端环境文件；请检查文件语法与权限。");
  const child = spawn(process.execPath, [server], {
    cwd: standalone,
    stdio: "inherit",
    env: {
      ...process.env,
      NODE_ENV: "production",
      PORT: configuration.port,
      // Shell/container HOSTNAME is often a machine ID; do not use it for binding.
      HOSTNAME: configuration.hostname,
    },
  });
  let stopping = false;
  let failed = false;
  let shutdownTimer;
  const forward = (signal) => {
    if (stopping) return;
    stopping = true;
    child.kill(signal);
    shutdownTimer = setTimeout(() => child.kill("SIGKILL"), 8000);
    shutdownTimer.unref();
  };
  const interrupt = () => forward("SIGINT");
  const terminate = () => forward("SIGTERM");
  process.on("SIGINT", interrupt);
  process.on("SIGTERM", terminate);
  child.once("error", () => {
    failed = true;
    console.error("ModSage 前端进程启动失败；请检查 Node.js 与构建文件权限。");
  });
  child.once("close", (code, signal) => {
    clearTimeout(shutdownTimer);
    process.off("SIGINT", interrupt);
    process.off("SIGTERM", terminate);
    process.exitCode = failed ? 1 : (code ?? 128 + (constants.signals[signal] ?? 1));
  });
}

try {
  const configuration = options(process.argv.slice(2));
  if (configuration === null) usage();
  else start(configuration);
} catch (error) {
  // Filesystem and module-loader errors can contain environment or private paths.
  console.error(error instanceof StartupError ? error.message : "无法准备前端生产服务；请检查构建、依赖和目录权限。");
  process.exitCode = 1;
}
