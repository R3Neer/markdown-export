import { copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const PLUGIN_ID = "mud-markdown-export";

export async function activatePlugin(communityFile) {
  let active = [];
  try {
    active = JSON.parse(await readFile(communityFile, "utf8"));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (!Array.isArray(active) || !active.every((value) => typeof value === "string")) {
    throw new Error(`${communityFile} no contiene una lista válida de plugins.`);
  }
  if (!active.includes(PLUGIN_ID)) active.push(PLUGIN_ID);
  const temporary = `${communityFile}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(active, null, 2)}\n`, "utf8");
  await rename(temporary, communityFile);
  return active;
}

export async function installLocal(pluginRoot) {
  const repositoryRoot = path.resolve(pluginRoot, "../../..");
  const configDirectory = path.join(repositoryRoot, ".obsidian");
  const target = path.join(configDirectory, "plugins", PLUGIN_ID);
  await mkdir(target, { recursive: true });
  await Promise.all([
    copyFile(path.join(pluginRoot, "dist", "main.js"), path.join(target, "main.js")),
    copyFile(path.join(pluginRoot, "manifest.json"), path.join(target, "manifest.json")),
    copyFile(path.join(pluginRoot, "styles.css"), path.join(target, "styles.css")),
  ]);
  const active = await activatePlugin(path.join(configDirectory, "community-plugins.json"));
  return { target, active };
}

const currentFile = fileURLToPath(import.meta.url);
if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === currentFile) {
  const pluginRoot = path.resolve(path.dirname(currentFile), "..");
  const result = await installLocal(pluginRoot);
  console.log(`Plugin instalado en ${result.target}`);
  console.log("Recarga Obsidian para cargar o actualizar el plugin.");
}
