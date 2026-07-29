import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  activatePlugin,
  copyPythonRuntime,
  PLUGIN_ID,
} from "../scripts/install-local.mjs";

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

  it("empaqueta el motor Python y una configuración genérica", async () => {
    temporary = await mkdtemp(path.join(os.tmpdir(), "markdown-export-runtime-"));
    const source = path.join(temporary, "source");
    const target = path.join(temporary, "target");
    await mkdir(source, { recursive: true });
    for (const filename of ["__init__.py", "__main__.py", "core.py", "web.py"]) {
      await writeFile(path.join(source, filename), `# ${filename}\n`, "utf8");
    }
    const config = path.join(temporary, "default-profiles.toml");
    await writeFile(config, "[profiles.markdown]\ninclude = [\".\"]\n", "utf8");

    const packageRoot = await copyPythonRuntime(source, config, target);
    await expect(readFile(path.join(packageRoot, "core.py"), "utf8"))
      .resolves.toBe("# core.py\n");
    await expect(readFile(path.join(packageRoot, "profiles.toml"), "utf8"))
      .resolves.toContain("profiles.markdown");
  });
});
