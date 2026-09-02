import esbuild from "esbuild";
import process from "node:process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = path.dirname(fileURLToPath(import.meta.url));

const production = process.argv[2] === "production";
const context = await esbuild.context({
  entryPoints: [path.join(packageRoot, "src", "main.ts")],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
  ],
  format: "cjs",
  platform: "node",
  target: "es2022",
  logLevel: "info",
  sourcemap: production ? false : "inline",
  treeShaking: true,
  minify: production,
  outfile: path.join(packageRoot, "dist", "main.js"),
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
