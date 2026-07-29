import { EventEmitter } from "node:events";
import path from "node:path";
import { PassThrough } from "node:stream";
import type { spawn } from "node:child_process";

import { describe, expect, it, vi } from "vitest";

import { PROTOCOL_VERSION } from "../src/protocol";
import {
  ExportServerManager,
  type ExporterChild,
  type HealthRequester,
} from "../src/server-manager";

class FakeChild extends EventEmitter {
  stdout = new PassThrough();
  stderr = new PassThrough();
  stdin = new PassThrough();
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  killed = false;

  kill(signal: NodeJS.Signals = "SIGTERM"): boolean {
    if (this.killed) return false;
    this.killed = true;
    this.signalCode = signal;
    queueMicrotask(() => this.emit("exit", null, signal));
    return true;
  }

  ready(root: string, port: number): void {
    this.stdout.write(`${JSON.stringify({
      event: "ready",
      url: `http://127.0.0.1:${port}/`,
      protocol_version: PROTOCOL_VERSION,
      root,
    })}\n`);
  }
}

function asChild(child: FakeChild): ExporterChild {
  return child as unknown as ExporterChild;
}

function health(root: string, contexts?: unknown[]): HealthRequester {
  return vi.fn(function (this: unknown) {
    contexts?.push(this);
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({
        status: "ok",
        protocol_version: PROTOCOL_VERSION,
        root,
      }),
    });
  });
}

function managerFor(
  root: string,
  children: FakeChild[],
  changes: { timeout?: number; onExit?: (message: string) => void; fetchContexts?: unknown[] } = {},
): ExportServerManager {
  const spawnProcess = vi.fn(() => {
    const child = new FakeChild();
    children.push(child);
    return asChild(child);
  }) as unknown as typeof spawn;
  return new ExportServerManager({
    pythonExecutable: "python",
    vaultRoot: root,
    startupTimeoutMs: changes.timeout ?? 100,
    stopGraceMs: 20,
    spawnProcess,
    fetchHealth: health(root, changes.fetchContexts),
    onUnexpectedExit: changes.onExit,
  });
}

describe("ExportServerManager", () => {
  it("comparte un único arranque y para al liberar la última vista", async () => {
    const root = path.resolve("vault");
    const children: FakeChild[] = [];
    const fetchContexts: unknown[] = [];
    const manager = managerFor(root, children, { fetchContexts });
    const first = manager.acquire();
    const second = manager.acquire();
    expect(children).toHaveLength(1);
    children[0]?.ready(root, 41001);
    await expect(first).resolves.toBe("http://127.0.0.1:41001/");
    await expect(second).resolves.toBe("http://127.0.0.1:41001/");
    expect(fetchContexts).toEqual([undefined]);
    expect(manager.referenceCount).toBe(2);
    await manager.release();
    expect(children[0]?.killed).toBe(false);
    await manager.release();
    expect(children[0]?.killed).toBe(true);
    expect(manager.referenceCount).toBe(0);
  });

  it("informa de una salida anterior al handshake y limpia el proceso", async () => {
    const root = path.resolve("vault");
    const children: FakeChild[] = [];
    const manager = managerFor(root, children);
    const start = manager.acquire();
    children[0]?.stderr.write("detalle útil\n");
    children[0]?.emit("exit", 2, null);
    await expect(start).rejects.toThrow(/detalle útil/u);
    expect(manager.referenceCount).toBe(0);
    expect(manager.url).toBeNull();
  });

  it("no se bloquea cuando el ejecutable de Python no existe", async () => {
    const root = path.resolve("vault");
    const child = new FakeChild();
    child.kill = () => false;
    const spawnProcess = vi.fn(() => {
      queueMicrotask(() => {
        child.emit("error", new Error("spawn python ENOENT"));
        child.emit("close", -2, null);
      });
      return asChild(child);
    }) as unknown as typeof spawn;
    const manager = new ExportServerManager({
      pythonExecutable: "python-inexistente",
      vaultRoot: root,
      startupTimeoutMs: 100,
      stopGraceMs: 5,
      spawnProcess,
      fetchHealth: health(root),
    });
    await expect(manager.acquire()).rejects.toThrow(/ENOENT/u);
    expect(manager.referenceCount).toBe(0);
  });

  it("aborta un arranque que supera el timeout", async () => {
    const root = path.resolve("vault");
    const children: FakeChild[] = [];
    const manager = managerFor(root, children, { timeout: 10 });
    await expect(manager.acquire()).rejects.toThrow(/no respondió/u);
    expect(children[0]?.killed).toBe(true);
  });

  it("notifica una muerte inesperada y puede arrancar de nuevo", async () => {
    const root = path.resolve("vault");
    const children: FakeChild[] = [];
    const onExit = vi.fn();
    const manager = managerFor(root, children, { onExit });
    const first = manager.acquire();
    children[0]?.ready(root, 41002);
    await first;
    children[0]?.emit("exit", 3, null);
    expect(onExit).toHaveBeenCalledOnce();
    expect(manager.referenceCount).toBe(0);
    const restarted = manager.acquire();
    expect(children).toHaveLength(2);
    children[1]?.ready(root, 41003);
    await expect(restarted).resolves.toBe("http://127.0.0.1:41003/");
    await manager.release();
  });
});
