import path from "node:path";

export const PROTOCOL_VERSION = 1;

export interface ReadyMessage {
  event: "ready";
  url: string;
  protocol_version: number;
  root: string;
}

export interface HealthMessage {
  status: "ok";
  protocol_version: number;
  root: string;
}

function normalizedPath(value: string): string {
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLocaleLowerCase() : resolved;
}

function requireRecord(value: unknown, description: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${description} is not a JSON object.`);
  }
  return value as Record<string, unknown>;
}

function validateRoot(value: unknown, expectedRoot: string): string {
  if (typeof value !== "string" || normalizedPath(value) !== normalizedPath(expectedRoot)) {
    throw new Error("The server responded for a different vault.");
  }
  return value;
}

export function parseReadyMessage(line: string, expectedRoot: string): ReadyMessage {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    throw new Error("The server did not emit a valid JSON readiness message.");
  }
  const record = requireRecord(parsed, "The readiness message");
  if (record.event !== "ready" || record.protocol_version !== PROTOCOL_VERSION) {
    throw new Error("The exporter protocol version is not compatible.");
  }
  if (typeof record.url !== "string") {
    throw new Error("The server did not provide a valid URL.");
  }
  const url = new URL(record.url);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1") {
    throw new Error("The exporter may only bind to 127.0.0.1 over HTTP.");
  }
  return {
    event: "ready",
    url: url.href,
    protocol_version: PROTOCOL_VERSION,
    root: validateRoot(record.root, expectedRoot),
  };
}

export function validateHealthMessage(value: unknown, expectedRoot: string): HealthMessage {
  const record = requireRecord(value, "The health response");
  if (record.status !== "ok" || record.protocol_version !== PROTOCOL_VERSION) {
    throw new Error("The started server does not implement the expected protocol.");
  }
  return {
    status: "ok",
    protocol_version: PROTOCOL_VERSION,
    root: validateRoot(record.root, expectedRoot),
  };
}
