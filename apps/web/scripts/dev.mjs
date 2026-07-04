import { spawn } from "node:child_process";

const hostname = process.env.HOSTNAME ?? "127.0.0.1";
const port = process.env.PORT ?? "3000";
const isWindows = process.platform === "win32";

const child = spawn("next", ["dev", "--hostname", hostname, "--port", port], {
  shell: isWindows,
  stdio: "inherit",
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    child.kill(signal);
  });
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
