import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { activatePlugin, PLUGIN_ID } from "../scripts/install-local.mjs";

let temporary;

afterEach(async () => {
  if (temporary) await rm(temporary, { recursive: true, force: true });
  temporary = undefined;
});

describe("instalación local", () => {
  it("activa el plugin sin borrar ni duplicar los existentes", async () => {
    temporary = await mkdtemp(path.join(os.tmpdir(), "mud-export-plugin-"));
    const config = path.join(temporary, "community-plugins.json");
    await import("node:fs/promises").then(({ writeFile }) =>
      writeFile(config, JSON.stringify(["obsidian-latex-suite"]), "utf8"),
    );
    await activatePlugin(config);
    await activatePlugin(config);
    const active = JSON.parse(await readFile(config, "utf8"));
    expect(active).toEqual(["obsidian-latex-suite", PLUGIN_ID]);
  });
});
