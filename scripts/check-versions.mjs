import { readFile } from "node:fs/promises";

const packageJson = JSON.parse(await readFile("package.json", "utf8"));
const manifest = JSON.parse(await readFile("manifest.json", "utf8"));
const versions = JSON.parse(await readFile("versions.json", "utf8"));
const pyproject = await readFile("pyproject.toml", "utf8");
const init = await readFile("src/markdown_export/__init__.py", "utf8");
const pythonVersion = pyproject.match(/^version = "([^"]+)"$/mu)?.[1];
const runtimeVersion = init.match(/^__version__ = "([^"]+)"$/mu)?.[1];
const expected = packageJson.version;
const values = [pythonVersion, runtimeVersion, manifest.version];
if (values.some((value) => value !== expected) || versions[expected] !== manifest.minAppVersion) {
  throw new Error(`Version metadata is inconsistent: ${JSON.stringify({ expected, pythonVersion, runtimeVersion, manifest, versions })}`);
}
console.log(`Version metadata is consistent at ${expected}.`);
