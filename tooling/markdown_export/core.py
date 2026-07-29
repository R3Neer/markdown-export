from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import unquote


ALWAYS_EXCLUDED = (".git/**", ".obsidian/**", ".trash/**", "exports/**")
FRONT_MATTER_DELIMITER = "---"
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(
    r"(!?)\[([^\]]*)\]\((<[^>]+>|[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "data:", "ftp://")


class ExportError(RuntimeError):
    """Error esperado y presentable durante la exportación."""


@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str
    source: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    title: str
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    follow_links: bool = False
    strip_frontmatter: bool = True
    source_markers: bool = True
    strict_links: bool = False
    max_chars: int = 0
    zip_tree: bool = False


@dataclass(frozen=True)
class ProjectConfig:
    config_path: Path
    root: Path
    output_dir: Path
    excludes: tuple[str, ...]
    default_profile: str
    profiles: dict[str, Profile]
    source_languages: tuple[tuple[str, str], ...] = ()
    personal_profile_names: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ExportOptions:
    root: Path
    output_dir: Path
    name: str
    title: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    follow_links: bool = False
    strip_frontmatter: bool = True
    source_markers: bool = True
    strict_links: bool = False
    max_chars: int = 0
    timestamp: bool = False
    zip_tree: bool = False
    output: Path | None = None
    source_languages: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExportPart:
    filename: str
    content: str
    documents: tuple[str, ...]


@dataclass(frozen=True)
class ExportResult:
    name: str
    title: str
    explicit_documents: tuple[str, ...]
    dependency_documents: tuple[str, ...]
    parts: tuple[ExportPart, ...]
    diagnostics: tuple[Diagnostic, ...]
    omitted_references: tuple[tuple[str, str], ...]

    @property
    def char_count(self) -> int:
        return sum(len(part.content) for part in self.parts)


@dataclass
class _Document:
    path: Path
    relative: str
    raw: str
    body: str
    title: str
    document_anchor: str
    heading_anchors: dict[str, str]
    heading_occurrences: list[tuple[str, str]]
    language: str | None = None


@dataclass
class _Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)
    _keys: set[tuple[object, ...]] = field(default_factory=set)

    def add(
        self,
        level: str,
        code: str,
        message: str,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        key = (level, code, message, source, target)
        if key in self._keys:
            return
        self._keys.add(key)
        self.items.append(Diagnostic(level, code, message, source, target))


def _as_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExportError(f"`{field_name}` debe ser una lista de cadenas.")
    return tuple(value)


def _source_languages(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or not all(
        isinstance(extension, str) and isinstance(language, str)
        for extension, language in value.items()
    ):
        raise ExportError("`export.source_languages` debe ser una tabla de cadenas.")
    normalized: dict[str, str] = {}
    for extension, language in value.items():
        suffix = f".{extension.lstrip('.')}".casefold()
        fence_language = language.strip()
        if suffix == ".md":
            raise ExportError("Markdown no puede redefinirse en `export.source_languages`.")
        if not re.fullmatch(r"\.[a-z0-9][a-z0-9_-]*", suffix):
            raise ExportError(f"Extensión inválida en `export.source_languages`: {extension}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", fence_language):
            raise ExportError(f"Lenguaje de bloque inválido en `export.source_languages`: {language}")
        if suffix in normalized:
            raise ExportError(f"Extensión duplicada en `export.source_languages`: {suffix}")
        normalized[suffix] = fence_language
    return tuple(sorted(normalized.items()))


def _profile_table(data: object, source: str) -> dict[str, Profile]:
    if not isinstance(data, dict):
        raise ExportError(f"La sección `[profiles]` de {source} debe contener tablas.")
    profiles: dict[str, Profile] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            raise ExportError(f"El perfil `{name}` de {source} debe ser una tabla.")
        title = raw.get("title", name)
        if not isinstance(title, str):
            raise ExportError(f"`profiles.{name}.title` de {source} debe ser una cadena.")
        profiles[name] = Profile(
            name=name,
            title=title,
            include=_as_tuple(raw.get("include"), f"profiles.{name}.include"),
            exclude=_as_tuple(raw.get("exclude"), f"profiles.{name}.exclude"),
            follow_links=bool(raw.get("follow_links", False)),
            strip_frontmatter=bool(raw.get("strip_frontmatter", True)),
            source_markers=bool(raw.get("source_markers", True)),
            strict_links=bool(raw.get("strict_links", False)),
            max_chars=int(raw.get("max_chars", 0)),
            zip_tree=bool(raw.get("zip_tree", False)),
        )
    return profiles


def _personal_profiles_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.stem}.local.toml")


def load_config(config_path: Path, root_override: Path | None = None) -> ProjectConfig:
    config_path = config_path.resolve()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ExportError(f"No existe la configuración: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ExportError(f"TOML inválido en {config_path}: {exc}") from exc

    export_data = data.get("export", {})
    if not isinstance(export_data, dict):
        raise ExportError("La sección `[export]` debe ser una tabla.")

    configured_root = export_data.get("root", "../..")
    if not isinstance(configured_root, str):
        raise ExportError("`export.root` debe ser una ruta.")
    root = (
        root_override.resolve()
        if root_override is not None
        else (config_path.parent / configured_root).resolve()
    )
    if not root.is_dir():
        raise ExportError(f"La raíz de fuentes no existe o no es una carpeta: {root}")

    output_value = export_data.get("output_dir", "exports")
    if not isinstance(output_value, str):
        raise ExportError("`export.output_dir` debe ser una ruta.")
    output_dir = (root / output_value).resolve()
    _ensure_within(output_dir, root, "El directorio de salida queda fuera de la raíz.")

    excludes = tuple(dict.fromkeys(ALWAYS_EXCLUDED + _as_tuple(export_data.get("exclude"), "export.exclude")))
    source_languages = _source_languages(export_data.get("source_languages"))
    default_profile = export_data.get("default_profile", "")
    if not isinstance(default_profile, str):
        raise ExportError("`export.default_profile` debe ser una cadena.")

    profiles = _profile_table(data.get("profiles", {}), str(config_path))
    personal_profile_names: set[str] = set()
    personal_path = _personal_profiles_path(config_path)
    if personal_path.exists():
        try:
            personal_data = tomllib.loads(personal_path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise ExportError(f"TOML inválido en {personal_path}: {exc}") from exc
        personal_profiles = _profile_table(
            personal_data.get("profiles", {}),
            str(personal_path),
        )
        profiles.update(personal_profiles)
        personal_profile_names.update(personal_profiles)
    if default_profile and default_profile not in profiles:
        raise ExportError(f"El perfil predeterminado `{default_profile}` no existe.")
    return ProjectConfig(
        config_path,
        root,
        output_dir,
        excludes,
        default_profile,
        profiles,
        source_languages,
        personal_profile_names,
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_string_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def save_personal_profile(config: ProjectConfig, profile: Profile) -> Path:
    if profile.name in config.profiles and profile.name not in config.personal_profile_names:
        raise ExportError(
            f"`{profile.name}` es un perfil compartido; elige otro nombre para el perfil personal."
        )
    profiles = {
        name: config.profiles[name]
        for name in config.personal_profile_names
        if name in config.profiles
    }
    profiles[profile.name] = profile
    lines = [
        "# Perfiles personales del exportador. Este archivo no se versiona.",
        "",
    ]
    for name in sorted(profiles):
        saved = profiles[name]
        lines.extend(
            [
                f"[profiles.{_toml_string(name)}]",
                f"title = {_toml_string(saved.title)}",
                f"include = {_toml_string_list(saved.include)}",
                f"exclude = {_toml_string_list(saved.exclude)}",
                f"follow_links = {str(saved.follow_links).lower()}",
                f"strip_frontmatter = {str(saved.strip_frontmatter).lower()}",
                f"source_markers = {str(saved.source_markers).lower()}",
                f"strict_links = {str(saved.strict_links).lower()}",
                f"max_chars = {saved.max_chars}",
                f"zip_tree = {str(saved.zip_tree).lower()}",
                "",
            ]
        )
    target = _personal_profiles_path(config.config_path)
    _atomic_write(target, "\n".join(lines))
    config.profiles[profile.name] = profile
    config.personal_profile_names.add(profile.name)
    return target


def options_from_profile(
    config: ProjectConfig,
    profile_name: str,
    *,
    includes: Iterable[str] | None = None,
    name: str | None = None,
    title: str | None = None,
    output: Path | None = None,
    follow_links: bool | None = None,
    strip_frontmatter: bool | None = None,
    source_markers: bool | None = None,
    strict_links: bool | None = None,
    max_chars: int | None = None,
    timestamp: bool = False,
    zip_tree: bool | None = None,
) -> ExportOptions:
    if profile_name:
        try:
            profile = config.profiles[profile_name]
        except KeyError as exc:
            raise ExportError(f"No existe el perfil `{profile_name}`.") from exc
    else:
        profile = Profile("", title or name or "Export Markdown", tuple(includes or ()))
    selected = tuple(includes) if includes is not None else profile.include
    export_name = _safe_name(name or profile.name or "export")
    return ExportOptions(
        root=config.root,
        output_dir=config.output_dir,
        name=export_name,
        title=title or profile.title,
        includes=selected,
        excludes=tuple(dict.fromkeys(config.excludes + profile.exclude)),
        follow_links=profile.follow_links if follow_links is None else follow_links,
        strip_frontmatter=profile.strip_frontmatter if strip_frontmatter is None else strip_frontmatter,
        source_markers=profile.source_markers if source_markers is None else source_markers,
        strict_links=profile.strict_links if strict_links is None else strict_links,
        max_chars=profile.max_chars if max_chars is None else max_chars,
        timestamp=timestamp,
        zip_tree=profile.zip_tree if zip_tree is None else zip_tree,
        output=output.resolve() if output is not None else None,
        source_languages=config.source_languages,
    )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    if not cleaned:
        raise ExportError("El nombre del export no contiene caracteres utilizables.")
    return cleaned


def _ensure_within(path: Path, root: Path, message: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ExportError(message) from exc


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_excluded(relative: str, patterns: Iterable[str]) -> bool:
    normalized = relative.strip("/")
    for pattern in patterns:
        pattern = pattern.replace("\\", "/").strip("/")
        if fnmatch.fnmatchcase(normalized.casefold(), pattern.casefold()):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if normalized.casefold() == prefix.casefold() or normalized.casefold().startswith(prefix.casefold() + "/"):
                return True
    return False


def _is_exportable_source(
    path: Path,
    source_languages: tuple[tuple[str, str], ...],
) -> bool:
    return path.suffix.casefold() == ".md" or path.suffix.casefold() in dict(source_languages)


class VaultIndex:
    def __init__(
        self,
        root: Path,
        excludes: tuple[str, ...],
        source_languages: tuple[tuple[str, str], ...] = (),
    ):
        self.root = root.resolve()
        self.excludes = excludes
        self.source_languages = source_languages
        self.files: list[Path] = []
        self.by_relative: dict[str, Path] = {}
        self.by_stem: dict[str, list[Path]] = defaultdict(list)
        for path in sorted(
            (
                candidate
                for candidate in self.root.rglob("*")
                if candidate.is_file() and _is_exportable_source(candidate, source_languages)
            ),
            key=lambda item: item.relative_to(self.root).as_posix().casefold(),
        ):
            _ensure_within(path, self.root, f"Una fuente indexada queda fuera de la raíz: {path}")
            relative = _relative(path, self.root)
            if _is_excluded(relative, excludes):
                continue
            resolved = path.resolve()
            self.files.append(resolved)
            self.by_relative[relative.casefold()] = resolved
            no_suffix = Path(relative).with_suffix("").as_posix().casefold()
            if path.suffix.casefold() == ".md" or no_suffix not in self.by_relative:
                self.by_relative[no_suffix] = resolved
            self.by_stem[path.stem.casefold()].append(resolved)

    def supports_target(self, target: str) -> bool:
        suffix = Path(unquote(target)).suffix.casefold()
        return suffix in {"", ".md"} or suffix in dict(self.source_languages)

    def resolve(self, target: str, source: Path) -> tuple[Path | None, str | None]:
        target = unquote(target.strip().replace("\\", "/"))
        if not target:
            return source.resolve(), None
        target = target.strip("/")
        exact = self.by_relative.get(target.casefold())
        if exact is not None:
            return exact, None

        source_parent = Path(_relative(source.parent, self.root))
        relative_key = (source_parent / target).as_posix()
        normalized = Path(os.path.normpath(relative_key)).as_posix().lstrip("./")
        exact = self.by_relative.get(normalized.casefold())
        if exact is not None:
            return exact, None

        target_path = Path(target)
        basename = (
            target_path.stem.casefold()
            if target_path.suffix
            else target_path.name.casefold()
        )
        candidates = self.by_stem.get(basename, [])
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            names = ", ".join(_relative(path, self.root) for path in candidates)
            return None, f"Destino ambiguo `{target}`: {names}"
        return None, f"No existe el documento `{target}`"


def select_paths(options: ExportOptions, index: VaultIndex) -> list[Path]:
    selected: list[Path] = []
    seen: set[Path] = set()
    for entry in options.includes:
        entry = entry.replace("\\", "/").strip()
        if not entry:
            continue
        _reject_unsafe_selection(entry)
        matches: list[Path] = []
        candidate = (options.root / entry).resolve()
        if candidate.exists():
            _ensure_within(candidate, options.root, f"La selección queda fuera de la raíz: {entry}")
            if candidate.is_file():
                matches = [candidate]
            elif candidate.is_dir():
                matches = sorted(
                    (
                        path
                        for path in candidate.rglob("*")
                        if path.is_file()
                        and _is_exportable_source(path, options.source_languages)
                    ),
                    key=lambda item: item.relative_to(options.root).as_posix().casefold(),
                )
        else:
            matches = sorted(
                options.root.glob(entry),
                key=lambda item: item.relative_to(options.root).as_posix().casefold(),
            )
        for path in matches:
            if not path.is_file() or not _is_exportable_source(path, options.source_languages):
                continue
            _ensure_within(path, options.root, f"La selección queda fuera de la raíz: {entry}")
            relative = _relative(path, options.root)
            if _is_excluded(relative, options.excludes):
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                selected.append(resolved)
    if not selected:
        raise ExportError("La selección no contiene fuentes exportables.")
    return selected


def _reject_unsafe_selection(entry: str) -> None:
    path = Path(entry)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ExportError(f"Selección insegura o fuera de la raíz: {entry}")


def strip_frontmatter(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_DELIMITER:
            return "\n".join(lines[index + 1 :]).lstrip("\n")
    return text


def _fenced_lines(text: str) -> Iterator[tuple[str, bool]]:
    marker: str | None = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        inside_before = marker is not None
        if match:
            token = match.group(1)
            if marker is None:
                marker = token[0]
            elif token[0] == marker:
                marker = None
            yield line, True
        else:
            yield line, inside_before


def _plain_heading(value: str) -> str:
    value = re.sub(r"\s+#+\s*$", "", value).strip()
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _heading_key(value: str) -> str:
    return _plain_heading(value).casefold()


def _slug(value: str) -> str:
    value = _plain_heading(value).casefold()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", value).strip("-") or "section"


def _document_anchor(relative: str) -> str:
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:10]
    return f"mud-doc-{_slug(relative)}-{digest}"


def _load_document(
    path: Path,
    root: Path,
    remove_frontmatter: bool,
    source_languages: tuple[tuple[str, str], ...],
) -> _Document:
    raw = path.read_text(encoding="utf-8-sig")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    language = dict(source_languages).get(path.suffix.casefold())
    body = strip_frontmatter(raw) if remove_frontmatter and language is None else raw
    title = path.name if language is not None else path.stem
    headings: list[tuple[str, str]] = []
    counts: dict[str, int] = defaultdict(int)
    relative = _relative(path, root)
    doc_anchor = _document_anchor(relative)
    for line, fenced in _fenced_lines(body if language is None else ""):
        if fenced:
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        plain = _plain_heading(match.group(2))
        if match.group(1) == "#" and title == path.stem:
            title = plain
        key = _heading_key(plain)
        counts[key] += 1
        suffix = f"-{counts[key]}" if counts[key] > 1 else ""
        headings.append((key, f"{doc_anchor}-{_slug(plain)}{suffix}"))
    heading_map: dict[str, str] = {}
    for key, anchor in headings:
        heading_map.setdefault(key, anchor)
    return _Document(
        path,
        relative,
        raw,
        body,
        title,
        doc_anchor,
        heading_map,
        headings,
        language,
    )


def _split_wikilink(value: str) -> tuple[str, str | None, str]:
    target_part, separator, label = value.partition("|")
    target, heading_separator, heading = target_part.partition("#")
    display = label if separator else (heading if heading_separator else Path(target).name)
    return target.strip(), heading.strip() if heading_separator else None, display.strip() or target.strip()


def _iter_local_references(
    text: str,
    index: VaultIndex,
) -> Iterator[tuple[str, str | None, str, bool]]:
    for line, fenced in _fenced_lines(text):
        if fenced:
            continue
        for match in WIKILINK_RE.finditer(line):
            target, heading, label = _split_wikilink(match.group(2))
            yield target, heading, label, bool(match.group(1))
        for match in MARKDOWN_LINK_RE.finditer(line):
            raw_target = match.group(3)
            if raw_target.startswith("<") and raw_target.endswith(">"):
                raw_target = raw_target[1:-1]
            if raw_target.casefold().startswith(EXTERNAL_SCHEMES):
                continue
            if raw_target.startswith("#"):
                continue
            target, separator, heading = raw_target.partition("#")
            if not index.supports_target(target):
                yield target, heading if separator else None, match.group(2) or Path(target).name, True
                continue
            yield target, heading if separator else None, match.group(2) or Path(target).stem, bool(match.group(1))


def _expand_dependencies(
    explicit: list[Path],
    index: VaultIndex,
    options: ExportOptions,
    diagnostics: _Diagnostics,
) -> list[Path]:
    if not options.follow_links:
        return explicit
    ordered = list(explicit)
    seen = set(explicit)
    queue = deque(explicit)
    while queue:
        source = queue.popleft()
        if source.suffix.casefold() != ".md":
            continue
        text = source.read_text(encoding="utf-8-sig")
        if options.strip_frontmatter:
            text = strip_frontmatter(text)
        for target, _heading, _label, embedded in _iter_local_references(text, index):
            if embedded:
                diagnostics.add("warning", "asset-not-supported", f"Embed o adjunto no exportado: `{target}`", _relative(source, options.root), target)
                continue
            resolved, error = index.resolve(target, source)
            if error:
                diagnostics.add("warning", "unresolved-link", error, _relative(source, options.root), target)
                continue
            if resolved is not None and resolved not in seen:
                seen.add(resolved)
                ordered.append(resolved)
                queue.append(resolved)
    return ordered


def _target_anchor(document: _Document, heading: str | None) -> str:
    if not heading:
        return document.document_anchor
    return document.heading_anchors.get(_heading_key(heading), document.document_anchor)


def _rewrite_line(
    line: str,
    source: _Document,
    included: dict[Path, _Document],
    index: VaultIndex,
    diagnostics: _Diagnostics,
    omitted: dict[tuple[str, str], None],
) -> str:
    # Los enlaces Markdown se procesan antes que los wikilinks. De lo contrario,
    # el enlace Markdown producido al expandir un wikilink se volvería a procesar
    # como si hubiese sido escrito en el documento fuente.
    def markdown_replace(match: re.Match[str]) -> str:
        embedded = bool(match.group(1))
        label = match.group(2)
        raw_target = match.group(3)
        if raw_target.startswith("<") and raw_target.endswith(">"):
            raw_target = raw_target[1:-1]
        if raw_target.casefold().startswith(EXTERNAL_SCHEMES):
            return match.group(0)
        if raw_target.startswith("#"):
            heading = raw_target[1:]
            return f"[{label}](#{_target_anchor(source, heading)})"
        target, separator, heading = raw_target.partition("#")
        is_document = index.supports_target(target)
        if embedded or not is_document:
            diagnostics.add("warning", "asset-not-supported", f"Adjunto no exportado: `{target}`", source.relative, target)
            return label or Path(target).name
        resolved, error = index.resolve(target, source.path)
        if error:
            diagnostics.add("warning", "unresolved-link", error, source.relative, target)
            return label
        if resolved in included:
            return f"[{label}](#{_target_anchor(included[resolved], heading if separator else None)})"
        target_label = _relative(resolved, index.root) if resolved is not None else target
        omitted[(label, target_label)] = None
        return label

    line = MARKDOWN_LINK_RE.sub(markdown_replace, line)

    def wiki_replace(match: re.Match[str]) -> str:
        embedded = bool(match.group(1))
        target, heading, label = _split_wikilink(match.group(2))
        if embedded:
            diagnostics.add("warning", "asset-not-supported", f"Embed o adjunto no exportado: `{target}`", source.relative, target)
            return label
        resolved, error = index.resolve(target, source.path)
        if error:
            diagnostics.add("warning", "unresolved-link", error, source.relative, target)
            return label
        if resolved in included:
            return f"[{label}](#{_target_anchor(included[resolved], heading)})"
        target_label = _relative(resolved, index.root) if resolved is not None else target
        omitted[(label, target_label)] = None
        return label

    line = WIKILINK_RE.sub(wiki_replace, line)
    return line


def _render_document(
    document: _Document,
    included: dict[Path, _Document],
    index: VaultIndex,
    options: ExportOptions,
    diagnostics: _Diagnostics,
    omitted: dict[tuple[str, str], None],
) -> str:
    rendered_title = _rewrite_line(
        document.title, document, included, index, diagnostics, omitted
    )
    output = [f'<a id="{document.document_anchor}"></a>', f"## {rendered_title}", ""]
    if options.source_markers:
        output.extend([f"> Fuente: `{document.relative}`", ""])

    if document.language is not None:
        longest_run = max(
            (len(match.group(0)) for match in re.finditer(r"`+", document.body)),
            default=0,
        )
        fence = "`" * max(3, longest_run + 1)
        output.extend(
            [
                f"{fence}{document.language}",
                document.body.rstrip("\n"),
                fence,
            ]
        )
        return "\n".join(output).strip() + "\n"

    fence_marker: str | None = None
    first_h1_removed = False
    heading_index = 0
    for line in document.body.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if fence_marker is None:
                fence_marker = token[0]
            elif token[0] == fence_marker:
                fence_marker = None
            output.append(line)
            continue
        if fence_marker is not None:
            output.append(line)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            if level == 1 and not first_h1_removed:
                first_h1_removed = True
                heading_index += 1
                continue
            anchor = document.heading_occurrences[heading_index][1]
            heading_index += 1
            rendered_heading = _rewrite_line(
                heading_match.group(2), document, included, index, diagnostics, omitted
            )
            output.append(f'<a id="{anchor}"></a>')
            output.append(f"{'#' * min(level + 1, 6)} {rendered_heading}")
            continue
        output.append(_rewrite_line(line, document, included, index, diagnostics, omitted))
    return "\n".join(output).strip() + "\n"


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _header(options: ExportOptions, commit: str | None, count: int, part: tuple[int, int] | None) -> str:
    lines = [f"# {options.title}", ""]
    if part is not None:
        lines.extend([f"Parte {part[0]} de {part[1]}", ""])
    lines.append(f"Documentos: {count}")
    if commit:
        lines.append(f"Commit de origen: `{commit}`")
    if options.timestamp:
        lines.append(f"Generado: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    return "\n".join(lines) + "\n\n"


def _appendices(
    omitted: dict[tuple[str, str], None],
    diagnostics: _Diagnostics,
) -> str:
    parts: list[str] = []
    if omitted:
        parts.extend(["## Referencias no incluidas", ""])
        for label, target in omitted:
            parts.append(f"- {label}: `{target}`")
        parts.append("")
    if diagnostics.items:
        parts.extend(["## Diagnósticos del export", ""])
        for item in diagnostics.items:
            location = f" ({item.source})" if item.source else ""
            parts.append(f"- **{item.level} · {item.code}**{location}: {item.message}")
        parts.append("")
    return "\n".join(parts).rstrip() + ("\n" if parts else "")


def _group_sections(
    sections: list[tuple[str, str]],
    max_chars: int,
    diagnostics: _Diagnostics,
) -> list[list[tuple[str, str]]]:
    if max_chars <= 0:
        return [sections]
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_size = 0
    for relative, content in sections:
        if len(content) > max_chars:
            diagnostics.add("warning", "oversized-document", f"`{relative}` supera por sí solo max_chars={max_chars}.", relative)
        if current and current_size + len(content) > max_chars:
            groups.append(current)
            current = []
            current_size = 0
        current.append((relative, content))
        current_size += len(content)
    if current:
        groups.append(current)
    return groups


def build_export(options: ExportOptions) -> ExportResult:
    options = replace(options, root=options.root.resolve(), output_dir=options.output_dir.resolve())
    _ensure_within(options.output_dir, options.root, "El directorio de salida queda fuera de la raíz.")
    if options.max_chars < 0:
        raise ExportError("`max_chars` no puede ser negativo.")

    # Las exclusiones de un perfil impiden seleccionar o seguir un documento,
    # pero no deben volverlo «inexistente»: aún puede aparecer como referencia
    # omitida. Solo los directorios internos que nunca son contenido se apartan
    # del índice de resolución.
    index = VaultIndex(options.root, ALWAYS_EXCLUDED, options.source_languages)
    explicit = select_paths(options, index)
    diagnostics = _Diagnostics()
    ordered = _expand_dependencies(explicit, index, options, diagnostics)
    documents = [
        _load_document(
            path,
            options.root,
            options.strip_frontmatter,
            options.source_languages,
        )
        for path in ordered
    ]
    included = {document.path.resolve(): document for document in documents}
    omitted: dict[tuple[str, str], None] = {}
    sections = [
        (
            document.relative,
            _render_document(document, included, index, options, diagnostics, omitted),
        )
        for document in documents
    ]

    if options.strict_links and any(item.code in {"unresolved-link", "asset-not-supported"} for item in diagnostics.items):
        messages = "; ".join(item.message for item in diagnostics.items if item.code in {"unresolved-link", "asset-not-supported"})
        raise ExportError(f"El modo estricto rechazó el export: {messages}")

    if options.zip_tree:
        parts = [
            ExportPart(
                document.relative,
                document.body if document.body.endswith("\n") else document.body + "\n",
                (document.relative,),
            )
            for document in documents
        ]
    else:
        groups = _group_sections(sections, options.max_chars, diagnostics)
        appendix = _appendices(omitted, diagnostics)
        commit = _git_commit(options.root)
        parts = []
        total = len(groups)
        for index_number, group in enumerate(groups, start=1):
            suffix = "" if total == 1 else f".part-{index_number:03d}"
            filename = f"{options.name}{suffix}.md"
            header = _header(options, commit, len(documents), None if total == 1 else (index_number, total))
            content = header + "\n".join(section for _relative_name, section in group)
            if index_number == total and appendix:
                content = content.rstrip() + "\n\n" + appendix
            parts.append(ExportPart(filename, content.rstrip() + "\n", tuple(relative for relative, _section in group)))

    explicit_set = set(explicit)
    return ExportResult(
        options.name,
        options.title,
        tuple(_relative(path, options.root) for path in explicit),
        tuple(_relative(path, options.root) for path in ordered if path not in explicit_set),
        tuple(parts),
        tuple(diagnostics.items),
        tuple(omitted.keys()),
    )


def _stage_write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            payload = content.encode("utf-8") if isinstance(content, str) else content
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_many(items: list[tuple[Path, str | bytes]]) -> None:
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, content in items:
            staged.append((target, _stage_write(target, content)))
        for target, temporary in staged:
            if target.exists():
                backup_handle = tempfile.NamedTemporaryFile(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".backup",
                    delete=False,
                )
                backup = Path(backup_handle.name)
                backup_handle.close()
                shutil.copy2(target, backup)
                backups[target] = backup
            os.replace(temporary, target)
            committed.append(target)
    except Exception:
        for target in reversed(committed):
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for _target, temporary in staged:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    _atomic_write_many([(path, content)])


def _zip_export(result: ExportResult) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for part in result.parts:
            info = zipfile.ZipInfo(part.filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                part.content.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return buffer.getvalue()


def _remove_superseded_outputs(target: Path, *, zipped: bool) -> None:
    stem_name = target.name[: -len(target.suffix)] if target.suffix else target.name
    stem = target.parent / stem_name
    alternative = stem.parent / f"{stem.name}{'.md' if zipped else '.zip'}"
    alternative.unlink(missing_ok=True)
    for legacy_part in stem.parent.glob(f"{stem.name}.part-*.md"):
        if legacy_part.is_file():
            legacy_part.unlink()


def write_export(options: ExportOptions, result: ExportResult) -> tuple[Path, ...]:
    base_output = options.output
    zipped = options.zip_tree or len(result.parts) > 1
    if base_output is not None:
        _ensure_within(base_output, options.root, "La salida queda fuera de la raíz del proyecto.")
        target = base_output.with_suffix(".zip") if zipped else base_output
    else:
        target = (
            options.output_dir / f"{result.name}.zip"
            if zipped
            else options.output_dir / result.parts[0].filename
        )
    content: str | bytes = _zip_export(result) if zipped else result.parts[0].content
    _atomic_write_many([(target, content)])
    _remove_superseded_outputs(target, zipped=zipped)
    return (target,)
