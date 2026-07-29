import path from "node:path";

import { describe, expect, it } from "vitest";

import { parseReadyMessage, PROTOCOL_VERSION, validateHealthMessage } from "../src/protocol";

const root = path.resolve("test-vault");

describe("protocolo del exportador", () => {
  it("acepta mensajes locales con la versión y raíz esperadas", () => {
    const ready = parseReadyMessage(
      JSON.stringify({
        event: "ready",
        url: "http://127.0.0.1:43210/",
        protocol_version: PROTOCOL_VERSION,
        root,
      }),
      root,
    );
    expect(ready.url).toBe("http://127.0.0.1:43210/");
    expect(validateHealthMessage({
      status: "ok",
      protocol_version: PROTOCOL_VERSION,
      root,
    }, root)).toEqual({
      status: "ok",
      protocol_version: PROTOCOL_VERSION,
      root,
    });
  });

  it("rechaza JSON inválido, servidores remotos, versiones y raíces incompatibles", () => {
    expect(() => parseReadyMessage("no-json", root)).toThrow(/JSON válido/u);
    expect(() => parseReadyMessage(JSON.stringify({
      event: "ready",
      url: "http://localhost:1234/",
      protocol_version: PROTOCOL_VERSION,
      root,
    }), root)).toThrow(/127\.0\.0\.1/u);
    expect(() => parseReadyMessage(JSON.stringify({
      event: "ready",
      url: "http://127.0.0.1:1234/",
      protocol_version: 999,
      root,
    }), root)).toThrow(/protocolo/u);
    expect(() => validateHealthMessage({
      status: "ok",
      protocol_version: PROTOCOL_VERSION,
      root: path.resolve("other-vault"),
    }, root)).toThrow(/bóveda distinta/u);
  });
});
