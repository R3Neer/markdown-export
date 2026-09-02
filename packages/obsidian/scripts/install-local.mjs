import { copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const PLUGIN_ID = "r3-markdown-export";
export const LEGACY_PLUGIN_ID = "mud-markdown-export";

export async function activatePlugin(communityFile) {
  let active = [];
  try {
    active = JSON.parse(await readFile(communityFile, "utf8"));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (!Array.isArray(active) || !active.every((value) => typeof value === "string")) {
    throw new Error(`${communityFile} does not contain a valid plugin list.`);
  }
  if (!active.includes(PLUGIN_ID)) active.push(PLUGIN_ID);
  const temporary = `${communityFile}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(active, null, 2)}\n`, "utf8");
  await rename(temporary, communityFile);
  return active;
}

export async function migrateLegacyData(configDirectory, target) {
  const legacyData = path.join(configDirectory, "plugins", LEGACY_PLUGIN_ID, "data.json");
  const targetData = path.join(target, "data.json");
  try {
    await readFile(targetData);
    return false;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  let legacy;
  try {
    legacy = JSON.parse(await readFile(legacyData, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
  const migrated = {
    exporterExecutable: "markdown-export",
    configPath: typeof legacy?.configPath === "string" ? legacy.configPath : "",
    defaultViewMode: legacy?.defaultViewMode === "tab" ? "tab" : "popout",
  };
  await writeFile(targetData, `${JSON.stringify(migrated, null, 2)}\n`, "utf8");
  return true;
}

export async function installLocal(pluginRoot, vaultRoot) {
  if (!vaultRoot) throw new Error("A vault path is required.");
  const repositoryRoot = path.resolve(pluginRoot, "../..");
  const configDirectory = path.join(path.resolve(vaultRoot), ".obsidian");
  const target = path.join(configDirectory, "plugins", PLUGIN_ID);
  await mkdir(target, { recursive: true });
  await Promise.all([
    copyFile(path.join(pluginRoot, "dist", "main.js"), path.join(target, "main.js")),
    copyFile(path.join(repositoryRoot, "manifest.json"), path.join(target, "manifest.json")),
    copyFile(path.join(pluginRoot, "styles.css"), path.join(target, "styles.css")),
  ]);
  const migratedLegacyData = await migrateLegacyData(configDirectory, target);
  const active = await activatePlugin(path.join(configDirectory, "community-plugins.json"));
  return { target, active, migratedLegacyData };
}

function vaultArgument(argv) {
  const index = argv.indexOf("--vault");
  return index >= 0 ? argv[index + 1] : undefined;
}

const currentFile = fileURLToPath(import.meta.url);
if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === currentFile) {
  const pluginRoot = path.resolve(path.dirname(currentFile), "..");
  const vaultRoot = vaultArgument(process.argv.slice(2));
  if (!vaultRoot) {
    console.error("Usage: node packages/obsidian/scripts/install-local.mjs --vault <vault-path>");
    process.exitCode = 2;
  } else {
    const result = await installLocal(pluginRoot, vaultRoot);
    console.log(`Plugin installed at ${result.target}`);
    if (result.migratedLegacyData) console.log("Legacy settings were migrated to the new plugin id.");
    console.log("Reload Obsidian and enable R3 Markdown Export if necessary.");
  }
}
