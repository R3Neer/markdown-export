from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import (
    ExportError,
    Profile,
    ProjectConfig,
    VaultIndex,
    _safe_name,
    build_export,
    options_from_profile,
    save_personal_profile,
    select_paths,
    write_export,
)

PROTOCOL_VERSION = 1


HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exportador Markdown</title>
<style>
:root { color-scheme: light dark; font: 16px system-ui, sans-serif; }
body { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
fieldset { margin: 1rem 0; } label { display: block; margin: .45rem 0; }
.layout { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
.profile-actions { display: flex; gap: .5rem; align-items: end; }
.profile-actions label { flex: 1; }
#savePanel { border: 1px solid #8886; padding: .7rem; margin: .6rem 0 1rem; }
#tree { max-height: 25rem; overflow: auto; border: 1px solid #8886; padding: .7rem; }
#tree label { overflow-wrap: anywhere; }
input[type=text], input[type=number], select { box-sizing: border-box; width: 100%; padding: .35rem; }
button { padding: .55rem 1rem; margin-right: .5rem; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #8886; padding: .8rem; }
@media (max-width: 760px) { .layout { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<h1>Exportador Markdown</h1>
<div class="layout">
<section>
<div class="profile-actions">
<label>Perfil <select id="profile"></select></label>
<button id="showSaveProfile" type="button">Guardar como perfil…</button>
</div>
<div id="savePanel" hidden>
  <label>Nombre del perfil <input id="profileName" type="text"></label>
  <label>Título visible <input id="profileTitle" type="text"></label>
  <button id="saveProfile" type="button">Guardar</button>
  <button id="cancelSaveProfile" type="button">Cancelar</button>
</div>
<label>Buscar <input id="search" type="text"></label>
<fieldset><legend>Documentos</legend><div id="tree"></div></fieldset>
</section>
<section>
<label>Nombre del export <input id="name" type="text" value="export"></label>
<label><input id="follow" type="checkbox"> Seguir enlaces</label>
<label><input id="strip" type="checkbox" checked> Retirar frontmatter</label>
<label><input id="markers" type="checkbox" checked> Marcadores de procedencia</label>
<label><input id="strict" type="checkbox"> Enlaces estrictos</label>
<label><input id="zipTree" type="checkbox"> ZIP con archivos separados (conservar carpetas)</label>
<label>Límite de caracteres (0 = sin límite) <input id="maxChars" type="number" min="0" value="0"></label>
<p><button id="preview">Previsualizar</button><button id="export">Exportar</button></p>
<pre id="result">Cargando…</pre>
</section>
</div>
<script>
"use strict";
const token = __TOKEN__;
const state = {
  files: [], profiles: {}, selected: new Set(),
  activeProfile: "", applyingProfile: false
};
const byId = id => document.getElementById(id);
async function api(path, options = {}) {
  options.headers = Object.assign({"Content-Type": "application/json"}, options.headers || {});
  if (options.method === "POST") options.headers["X-Export-Token"] = token;
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
function setText(element, value) { element.textContent = value; }
function renderFiles() {
  const tree = byId("tree");
  tree.replaceChildren();
  const query = byId("search").value.toLocaleLowerCase();
  const visible = state.files.filter(path => path.toLocaleLowerCase().includes(query));
  const root = {};
  for (const path of visible) {
    let node = root;
    const parts = path.split("/");
    for (const directory of parts.slice(0, -1)) node = (node[directory] ||= {});
    (node.__files ||= []).push(path);
  }
  function appendBranch(parent, node) {
    for (const name of Object.keys(node).filter(x => x !== "__files").sort()) {
      const details = document.createElement("details"); details.open = true;
      const summary = document.createElement("summary");
      const folder = document.createElement("input"); folder.type = "checkbox";
      const descendants = [];
      function collect(value) {
        descendants.push(...(value.__files || []));
        for (const key of Object.keys(value).filter(x => x !== "__files")) collect(value[key]);
      }
      collect(node[name]);
      const selectedCount = descendants.filter(path => state.selected.has(path)).length;
      folder.checked = selectedCount === descendants.length && descendants.length > 0;
      folder.indeterminate = selectedCount > 0 && selectedCount < descendants.length;
      folder.addEventListener("change", event => {
        event.stopPropagation();
        for (const path of descendants) folder.checked ? state.selected.add(path) : state.selected.delete(path);
        renderFiles(); updateProfileState();
      });
      summary.append(folder, document.createTextNode(" " + name));
      details.append(summary);
      const contents = document.createElement("div"); contents.style.paddingLeft = "1.2rem";
      appendBranch(contents, node[name]); details.append(contents); parent.append(details);
    }
    for (const path of node.__files || []) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox"; input.value = path; input.className = "file";
      input.checked = state.selected.has(path);
      input.addEventListener("change", () => {
        input.checked ? state.selected.add(path) : state.selected.delete(path);
        renderFiles(); updateProfileState();
      });
      label.append(input, document.createTextNode(" " + path.split("/").at(-1)));
      parent.append(label);
    }
  }
  appendBranch(tree, root);
}
function renderProfileOptions(selected = "") {
  const select = byId("profile");
  select.replaceChildren();
  const custom = document.createElement("option");
  custom.value = ""; setText(custom, "Personalizado (sin guardar)");
  custom.disabled = true;
  select.append(custom);
  for (const profile of Object.values(state.profiles)) {
    const option = document.createElement("option");
    option.value = profile.name;
    setText(option, profile.title + (profile.personal ? " · personal" : ""));
    select.append(option);
  }
  select.value = selected;
}
function profileMatches(profile) {
  if (!profile) return false;
  const files = state.files.filter(path => state.selected.has(path));
  return byId("name").value === profile.name
    && byId("follow").checked === profile.follow_links
    && byId("strip").checked === profile.strip_frontmatter
    && byId("markers").checked === profile.source_markers
    && byId("strict").checked === profile.strict_links
    && byId("zipTree").checked === profile.zip_tree
    && Number(byId("maxChars").value || 0) === profile.max_chars
    && JSON.stringify(files) === JSON.stringify(profile.files);
}
function updateProfileState() {
  if (state.applyingProfile) return;
  byId("profile").value = profileMatches(state.profiles[state.activeProfile])
    ? state.activeProfile
    : "";
}
function applyProfile() {
  const selected = byId("profile").value;
  const profile = state.profiles[selected];
  if (!profile) return;
  state.applyingProfile = true;
  state.activeProfile = selected;
  byId("name").value = profile.name;
  byId("follow").checked = profile.follow_links;
  byId("strip").checked = profile.strip_frontmatter;
  byId("markers").checked = profile.source_markers;
  byId("strict").checked = profile.strict_links;
  byId("zipTree").checked = profile.zip_tree;
  byId("maxChars").value = profile.max_chars;
  state.selected = new Set(profile.files);
  renderFiles();
  state.applyingProfile = false;
  updateProfileState();
}
function payload() {
  const files = state.files.filter(path => state.selected.has(path));
  return {
    profile: byId("profile").value, files, name: byId("name").value,
    follow_links: byId("follow").checked, strip_frontmatter: byId("strip").checked,
    source_markers: byId("markers").checked, strict_links: byId("strict").checked,
    zip_tree: byId("zipTree").checked, max_chars: Number(byId("maxChars").value || 0)
  };
}
function showSaveProfile() {
  const base = state.profiles[state.activeProfile];
  byId("profileName").value = byId("name").value || "perfil";
  byId("profileTitle").value = base
    ? base.title + " personalizado"
    : byId("name").value || "Perfil personalizado";
  byId("savePanel").hidden = false;
  byId("profileName").focus();
}
async function saveCurrentProfile() {
  setText(byId("result"), "Guardando perfil…");
  try {
    const request = Object.assign(payload(), {
      profile_name: byId("profileName").value,
      profile_title: byId("profileTitle").value
    });
    const data = await api("/api/profiles/save", {
      method: "POST", body: JSON.stringify(request)
    });
    state.profiles = data.profiles;
    state.activeProfile = data.saved_profile;
    byId("name").value = data.saved_profile;
    renderProfileOptions(state.activeProfile);
    byId("savePanel").hidden = true;
    updateProfileState();
    setText(byId("result"), "Perfil personal guardado.");
  } catch (error) { setText(byId("result"), "Error: " + error.message); }
}
async function run(action) {
  setText(byId("result"), "Procesando…");
  try {
    const data = await api("/api/" + action, {method: "POST", body: JSON.stringify(payload())});
    const lines = [
      action === "export" ? "Export completado." : "Previsualización.",
      "Explícitos: " + data.explicit.length,
      "Dependencias: " + data.dependencies.length,
      "Partes: " + data.parts.length,
      "Caracteres: " + data.characters
    ];
    if (data.outputs) lines.push("Salidas:\\n" + data.outputs.join("\\n"));
    if (data.diagnostics.length) lines.push("Avisos:\\n" + data.diagnostics.map(x => x.level + " " + x.code + ": " + x.message).join("\\n"));
    setText(byId("result"), lines.join("\\n"));
  } catch (error) { setText(byId("result"), "Error: " + error.message); }
}
async function start() {
  const data = await api("/api/tree");
  state.files = data.files; state.profiles = data.profiles;
  state.activeProfile = data.default_profile;
  renderProfileOptions(state.activeProfile);
  applyProfile(); setText(byId("result"), "Listo.");
}
byId("search").addEventListener("input", renderFiles);
byId("profile").addEventListener("change", applyProfile);
for (const id of ["name", "follow", "strip", "markers", "strict", "zipTree", "maxChars"]) {
  byId(id).addEventListener("input", updateProfileState);
  byId(id).addEventListener("change", updateProfileState);
}
byId("showSaveProfile").addEventListener("click", showSaveProfile);
byId("saveProfile").addEventListener("click", saveCurrentProfile);
byId("cancelSaveProfile").addEventListener("click", () => {
  byId("savePanel").hidden = true;
});
byId("preview").addEventListener("click", () => run("preview"));
byId("export").addEventListener("click", () => run("export"));
start().catch(error => setText(byId("result"), "Error: " + error.message));
</script>
</body>
</html>"""


def _profile_files(config: ProjectConfig, name: str) -> list[str]:
    options = options_from_profile(config, name)
    index = VaultIndex(config.root, options.excludes, config.source_languages)
    try:
        return [path.relative_to(config.root).as_posix() for path in select_paths(options, index)]
    except ExportError:
        return []


def _tree_payload(config: ProjectConfig) -> dict[str, Any]:
    index = VaultIndex(config.root, config.excludes, config.source_languages)
    profiles = {
        name: {
            "name": name,
            "title": profile.title,
            "files": _profile_files(config, name),
            "follow_links": profile.follow_links,
            "strip_frontmatter": profile.strip_frontmatter,
            "source_markers": profile.source_markers,
            "strict_links": profile.strict_links,
            "max_chars": profile.max_chars,
            "zip_tree": profile.zip_tree,
            "personal": name in config.personal_profile_names,
        }
        for name, profile in config.profiles.items()
    }
    return {
        "files": [path.relative_to(config.root).as_posix() for path in index.files],
        "profiles": profiles,
        "default_profile": config.default_profile,
    }


def _result_payload(result) -> dict[str, Any]:
    return {
        "explicit": list(result.explicit_documents),
        "dependencies": list(result.dependency_documents),
        "parts": [
            {"filename": part.filename, "characters": len(part.content), "documents": list(part.documents)}
            for part in result.parts
        ],
        "characters": result.char_count,
        "diagnostics": [diagnostic.__dict__ for diagnostic in result.diagnostics],
    }


def create_server(
    config: ProjectConfig,
    *,
    port: int = 8765,
    token: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    session_token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: HTTPStatus, value: object) -> None:
            content = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            if self.path == "/":
                content = HTML.replace("__TOKEN__", json.dumps(session_token)).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if self.path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "protocol_version": PROTOCOL_VERSION,
                        "root": str(config.root),
                    },
                )
                return
            if self.path in {"/api/tree", "/api/profiles"}:
                self._json(HTTPStatus.OK, _tree_payload(config))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Recurso inexistente."})

        def do_POST(self) -> None:
            if self.headers.get("X-Export-Token") != session_token:
                self._json(HTTPStatus.FORBIDDEN, {"error": "Token de sesión inválido."})
                return
            if self.path not in {"/api/preview", "/api/export", "/api/profiles/save"}:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Recurso inexistente."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2_000_000:
                    raise ExportError("La petición es demasiado grande.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ExportError("La petición debe ser un objeto JSON.")
                files = payload.get("files")
                if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
                    raise ExportError("`files` debe ser una lista de rutas.")
                profile = payload.get("profile", "")
                if not isinstance(profile, str):
                    raise ExportError("`profile` debe ser una cadena.")
                if self.path == "/api/profiles/save":
                    profile_name = _safe_name(str(payload.get("profile_name", "")))
                    profile_title = str(payload.get("profile_title", "")).strip()
                    if not profile_title:
                        raise ExportError("El título visible del perfil no puede estar vacío.")
                    max_chars = int(payload.get("max_chars", 0))
                    if max_chars < 0:
                        raise ExportError("`max_chars` no puede ser negativo.")
                    validation_options = options_from_profile(
                        config,
                        profile,
                        includes=files,
                        name=profile_name,
                        follow_links=bool(payload.get("follow_links", False)),
                        strip_frontmatter=bool(payload.get("strip_frontmatter", True)),
                        source_markers=bool(payload.get("source_markers", True)),
                        strict_links=bool(payload.get("strict_links", False)),
                        max_chars=max_chars,
                        zip_tree=bool(payload.get("zip_tree", False)),
                    )
                    select_paths(
                        validation_options,
                        VaultIndex(
                            config.root,
                            validation_options.excludes,
                            config.source_languages,
                        ),
                    )
                    base_excludes = (
                        config.profiles[profile].exclude
                        if profile in config.profiles
                        else ()
                    )
                    save_personal_profile(
                        config,
                        Profile(
                            name=profile_name,
                            title=profile_title,
                            include=tuple(files),
                            exclude=base_excludes,
                            follow_links=bool(payload.get("follow_links", False)),
                            strip_frontmatter=bool(payload.get("strip_frontmatter", True)),
                            source_markers=bool(payload.get("source_markers", True)),
                            strict_links=bool(payload.get("strict_links", False)),
                            max_chars=max_chars,
                            zip_tree=bool(payload.get("zip_tree", False)),
                        ),
                    )
                    response = _tree_payload(config)
                    response["saved_profile"] = profile_name
                    self._json(HTTPStatus.OK, response)
                    return
                options = options_from_profile(
                    config,
                    profile,
                    includes=files,
                    name=str(payload.get("name", "export")),
                    follow_links=bool(payload.get("follow_links", False)),
                    strip_frontmatter=bool(payload.get("strip_frontmatter", True)),
                    source_markers=bool(payload.get("source_markers", True)),
                    strict_links=bool(payload.get("strict_links", False)),
                    max_chars=int(payload.get("max_chars", 0)),
                    zip_tree=bool(payload.get("zip_tree", False)),
                )
                result = build_export(options)
                response = _result_payload(result)
                if self.path == "/api/export":
                    response["outputs"] = [str(path) for path in write_export(options, result)]
                self._json(HTTPStatus.OK, response)
            except (ExportError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, session_token


def _ready_payload(config: ProjectConfig, server: ThreadingHTTPServer) -> dict[str, Any]:
    return {
        "event": "ready",
        "url": f"http://127.0.0.1:{server.server_port}/",
        "protocol_version": PROTOCOL_VERSION,
        "root": str(config.root),
    }


def serve(
    config: ProjectConfig,
    *,
    port: int = 8765,
    open_browser: bool = True,
    ready_json: bool = False,
) -> None:
    server, _token = create_server(config, port=port)
    payload = _ready_payload(config, server)
    url = str(payload["url"])
    if ready_json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        print(f"Exportador disponible en {url}", flush=True)
        print("Pulsa Ctrl+C para detenerlo.", flush=True)
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
