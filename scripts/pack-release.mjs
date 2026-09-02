import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";

const target = path.resolve("release", "obsidian");
await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await Promise.all([
  cp("packages/obsidian/dist/main.js", path.join(target, "main.js")),
  cp("manifest.json", path.join(target, "manifest.json")),
  cp("packages/obsidian/styles.css", path.join(target, "styles.css")),
]);
console.log(`Release assets prepared at ${target}`);
