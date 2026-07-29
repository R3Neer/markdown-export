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
    throw new Error(`${description} no es un objeto JSON.`);
  }
  return value as Record<string, unknown>;
}

function validateRoot(value: unknown, expectedRoot: string): string {
  if (typeof value !== "string" || normalizedPath(value) !== normalizedPath(expectedRoot)) {
    throw new Error("El servidor respondió para una bóveda distinta.");
  }
  return value;
}

export function parseReadyMessage(line: string, expectedRoot: string): ReadyMessage {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    throw new Error("El servidor no emitió un mensaje de disponibilidad JSON válido.");
  }
  const record = requireRecord(parsed, "El mensaje de disponibilidad");
  if (record.event !== "ready" || record.protocol_version !== PROTOCOL_VERSION) {
    throw new Error("La versión del protocolo del exportador no es compatible.");
  }
  if (typeof record.url !== "string") {
    throw new Error("El servidor no indicó una URL válida.");
  }
  const url = new URL(record.url);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1") {
    throw new Error("El exportador solo puede enlazarse a 127.0.0.1 mediante HTTP.");
  }
  return {
    event: "ready",
    url: url.href,
    protocol_version: PROTOCOL_VERSION,
    root: validateRoot(record.root, expectedRoot),
  };
}

export function validateHealthMessage(value: unknown, expectedRoot: string): HealthMessage {
  const record = requireRecord(value, "La respuesta de salud");
  if (record.status !== "ok" || record.protocol_version !== PROTOCOL_VERSION) {
    throw new Error("El servidor iniciado no implementa el protocolo esperado.");
  }
  return {
    status: "ok",
    protocol_version: PROTOCOL_VERSION,
    root: validateRoot(record.root, expectedRoot),
  };
}
