import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  activatePlugin,
  LEGACY_PLUGIN_ID,
  migrateLegacyData,
  PLUGIN_ID,
} from "../scripts/install-local.mjs";

let temporary;

afterEach(async () => {
  if (temporary) await rm(temporary, { recursive: true, force: true });
  temporary = undefined;
});

describe("local installation", () => {
  it("activates the new plugin without removing or duplicating existing plugins", async () => {
    temporary = await mkdtemp(path.join(os.tmpdir(), "r3-markdown-export-plugin-"));
    const config = path.join(temporary, "community-plugins.json");
    await writeFile(config, JSON.stringify(["obsidian-latex-suite", LEGACY_PLUGIN_ID]), "utf8");
    await activatePlugin(config);
    await activatePlugin(config);
    const active = JSON.parse(await readFile(config, "utf8"));
    expect(active).toEqual(["obsidian-latex-suite", LEGACY_PLUGIN_ID, PLUGIN_ID]);
  });

  it("migrates compatible legacy settings without retaining the Python executable", async () => {
    temporary = await mkdtemp(path.join(os.tmpdir(), "r3-markdown-export-settings-"));
    const configDirectory = path.join(temporary, ".obsidian");
    const legacy = path.join(configDirectory, "plugins", LEGACY_PLUGIN_ID);
    const target = path.join(configDirectory, "plugins", PLUGIN_ID);
    await mkdir(legacy, { recursive: true });
    await mkdir(target, { recursive: true });
    await writeFile(path.join(legacy, "data.json"), JSON.stringify({
      pythonExecutable: "custom-python",
      configPath: "markdown-export.toml",
      defaultViewMode: "tab",
    }), "utf8");

    await expect(migrateLegacyData(configDirectory, target)).resolves.toBe(true);
    const migrated = JSON.parse(await readFile(path.join(target, "data.json"), "utf8"));
    expect(migrated).toEqual({
      exporterExecutable: "markdown-export",
      configPath: "markdown-export.toml",
      defaultViewMode: "tab",
    });
    await expect(migrateLegacyData(configDirectory, target)).resolves.toBe(false);
  });
});
