import { spawn, type ChildProcessByStdio } from "node:child_process";
import { get as httpGet } from "node:http";
import type { Readable } from "node:stream";

import { parseReadyMessage, validateHealthMessage } from "./protocol";

export interface HealthResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type HealthRequester = (url: URL) => Promise<HealthResponse>;

export interface ServerManagerOptions {
  exporterExecutable: string;
  vaultRoot: string;
  configPath?: string;
  startupTimeoutMs?: number;
  stopGraceMs?: number;
  spawnProcess?: typeof spawn;
  fetchHealth?: HealthRequester;
  onUnexpectedExit?: (message: string) => void;
}

export type ExporterChild = ChildProcessByStdio<null, Readable, Readable>;

function requestHealthOverHttp(url: URL): Promise<HealthResponse> {
  return new Promise((resolve, reject) => {
    const request = httpGet(url, { headers: { Accept: "application/json" } }, (response) => {
      const chunks: Buffer[] = [];
      let size = 0;
      response.on("data", (chunk: Buffer | string) => {
        const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        size += data.length;
        if (size > 65_536) {
          request.destroy(new Error("The health response is too large."));
          return;
        }
        chunks.push(data);
      });
      response.once("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        const status = response.statusCode ?? 0;
        resolve({
          ok: status >= 200 && status < 300,
          status,
          json: () => Promise.resolve(JSON.parse(body) as unknown),
        });
      });
    });
    request.setTimeout(3_000, () => {
      request.destroy(new Error("The health check exceeded 3000 ms."));
    });
    request.once("error", reject);
  });
}

export class ExportServerManager {
  private readonly options: Required<
    Pick<ServerManagerOptions, "startupTimeoutMs" | "stopGraceMs" | "spawnProcess" | "fetchHealth">
  > &
    ServerManagerOptions;
  private child: ExporterChild | null = null;
  private startPromise: Promise<string> | null = null;
  private serverUrl: string | null = null;
  private consumers = 0;
  private stopping = false;
  private stderrLines: string[] = [];

  constructor(options: ServerManagerOptions) {
    this.options = {
      ...options,
      startupTimeoutMs: options.startupTimeoutMs ?? 10_000,
      stopGraceMs: options.stopGraceMs ?? 2_000,
      spawnProcess: options.spawnProcess ?? spawn,
      fetchHealth: options.fetchHealth ?? requestHealthOverHttp,
    };
  }

  get referenceCount(): number {
    return this.consumers;
  }

  get url(): string | null {
    return this.serverUrl;
  }

  get running(): boolean {
    return this.serverUrl !== null;
  }

  async check(): Promise<string> {
    const url = await this.acquire();
    await this.release();
    return url;
  }

  async acquire(): Promise<string> {
    this.consumers += 1;
    try {
      return await this.ensureStarted();
    } catch (error) {
      this.consumers = Math.max(0, this.consumers - 1);
      if (this.consumers === 0) await this.stopProcess();
      throw error;
    }
  }

  async release(): Promise<void> {
    this.consumers = Math.max(0, this.consumers - 1);
    if (this.consumers === 0) await this.stopProcess();
  }

  async stopAll(): Promise<void> {
    this.consumers = 0;
    await this.stopProcess();
  }

  private async ensureStarted(): Promise<string> {
    if (this.serverUrl !== null) return this.serverUrl;
    if (this.startPromise !== null) return this.startPromise;
    this.startPromise = this.start();
    try {
      return await this.startPromise;
    } finally {
      this.startPromise = null;
    }
  }

  private async start(): Promise<string> {
    this.stderrLines = [];
    const args = [
      "serve",
      "--root",
      this.options.vaultRoot,
      "--port",
      "0",
      "--no-browser",
      "--ready-json",
    ];
    if (this.options.configPath) args.push("--config", this.options.configPath);
    const child = this.options.spawnProcess(this.options.exporterExecutable, args, {
      cwd: this.options.vaultRoot,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string | Buffer) => this.captureStderr(String(chunk)));

    const ready = await this.waitUntilReady(child);
    const fetchHealth = this.options.fetchHealth;
    const response = await fetchHealth(new URL("api/health", ready.url));
    if (!response.ok) throw new Error(`The health check returned HTTP ${response.status}.`);
    validateHealthMessage(await response.json(), this.options.vaultRoot);
    if (this.child !== child) throw new Error("The server closed during start-up.");
    this.serverUrl = ready.url;
    return ready.url;
  }

  private waitUntilReady(child: ExporterChild): Promise<ReturnType<typeof parseReadyMessage>> {
    return new Promise((resolve, reject) => {
      let buffer = "";
      let settled = false;
      const finish = (error?: Error, line?: string): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        child.stdout.off("data", onData);
        child.off("error", onError);
        if (error !== undefined) reject(error);
        else {
          try {
            resolve(parseReadyMessage(line ?? "", this.options.vaultRoot));
          } catch (parseError) {
            reject(parseError instanceof Error ? parseError : new Error(String(parseError)));
          }
        }
      };
      const onData = (chunk: string | Buffer): void => {
        buffer += String(chunk);
        const newline = buffer.indexOf("\n");
        if (newline >= 0) finish(undefined, buffer.slice(0, newline).trim());
      };
      const onError = (error: Error): void => finish(error);
      const timer = setTimeout(
        () => finish(new Error(`The server did not respond within ${this.options.startupTimeoutMs} ms.`)),
        this.options.startupTimeoutMs,
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", onData);
      child.once("error", onError);
      child.once("exit", (code, signal) => {
        if (!settled) {
          finish(new Error(this.exitMessage(code, signal, "The server exited before it became available.")));
          return;
        }
        this.handleExit(child, code, signal);
      });
    });
  }

  private handleExit(
    child: ExporterChild,
    code: number | null,
    signal: NodeJS.Signals | null,
  ): void {
    if (this.child !== child) return;
    this.child = null;
    this.serverUrl = null;
    if (this.stopping) return;
    const hadConsumers = this.consumers > 0;
    this.consumers = 0;
    if (hadConsumers) {
      this.options.onUnexpectedExit?.(this.exitMessage(code, signal, "The server closed unexpectedly."));
    }
  }

  private exitMessage(code: number | null, signal: NodeJS.Signals | null, prefix: string): string {
    const reason = signal !== null ? `signal ${signal}` : `exit code ${String(code)}`;
    const details = this.stderrLines.length > 0 ? `\n${this.stderrLines.join("\n")}` : "";
    return `${prefix} (${reason}).${details}`;
  }

  private captureStderr(chunk: string): void {
    for (const line of chunk.split(/\r?\n/u).filter(Boolean)) {
      this.stderrLines.push(line);
    }
    this.stderrLines = this.stderrLines.slice(-20);
  }

  private async stopProcess(): Promise<void> {
    const child = this.child;
    this.serverUrl = null;
    if (child === null) return;
    this.stopping = true;
    try {
      const exited = new Promise<void>((resolve) => {
        const done = (): void => {
          child.off("exit", done);
          child.off("close", done);
          resolve();
        };
        child.once("exit", done);
        child.once("close", done);
      });
      child.kill();
      const graceful = await Promise.race([
        exited.then(() => true),
        new Promise<boolean>((resolve) => setTimeout(() => resolve(false), this.options.stopGraceMs)),
      ]);
      if (!graceful && child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
        await Promise.race([
          exited,
          new Promise<void>((resolve) => setTimeout(resolve, this.options.stopGraceMs)),
        ]);
      }
    } finally {
      if (this.child === child) this.child = null;
      this.stopping = false;
    }
  }
}
