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
from importlib.resources import files
from pathlib import Path, PurePosixPath
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
    """Expected, user-facing export failure."""


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
    index_excludes: tuple[str, ...] = ALWAYS_EXCLUDED
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
        raise ExportError(f"`{field_name}` must be an array of strings.")
    return tuple(value)


def _source_languages(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or not all(
        isinstance(extension, str) and isinstance(language, str)
        for extension, language in value.items()
    ):
        raise ExportError("`export.source_languages` must be a table of strings.")
    normalized: dict[str, str] = {}
    for extension, language in value.items():
        suffix = f".{extension.lstrip('.')}".casefold()
        fence_language = language.strip()
        if suffix == ".md":
            raise ExportError("Markdown cannot be redefined in `export.source_languages`.")
        if not re.fullmatch(r"\.[a-z0-9][a-z0-9_-]*", suffix):
            raise ExportError(f"Invalid extension in `export.source_languages`: {extension}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", fence_language):
            raise ExportError(f"Invalid fence language in `export.source_languages`: {language}")
        if suffix in normalized:
            raise ExportError(f"Duplicate extension in `export.source_languages`: {suffix}")
        normalized[suffix] = fence_language
    return tuple(sorted(normalized.items()))


def _profile_table(data: object, source: str) -> dict[str, Profile]:
    if not isinstance(data, dict):
        raise ExportError(f"The `[profiles]` section in {source} must contain tables.")
    profiles: dict[str, Profile] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict):
            raise ExportError(f"Profile `{name}` in {source} must be a table.")
        title = raw.get("title", name)
        if not isinstance(title, str):
            raise ExportError(f"`profiles.{name}.title` in {source} must be a string.")
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


def load_config(config_path: Path | None = None, root_override: Path | None = None) -> ProjectConfig:
    try:
        if config_path is None:
            root = (root_override or Path.cwd()).resolve()
            effective_path = root / ".markdown-export.toml"
            source = "the built-in configuration"
            data = tomllib.loads(files("markdown_export").joinpath("default.toml").read_text(encoding="utf-8"))
        else:
            effective_path = config_path.resolve()
            source = str(effective_path)
            data = tomllib.loads(effective_path.read_text(encoding="utf-8-sig"))
            root = Path()
    except FileNotFoundError as exc:
        raise ExportError(f"Configuration does not exist: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ExportError(f"Invalid TOML in {source}: {exc}") from exc

    export_data = data.get("export", {})
    if not isinstance(export_data, dict):
        raise ExportError("The `[export]` section must be a table.")

    configured_root = export_data.get("root", ".")
    if not isinstance(configured_root, str):
        raise ExportError("`export.root` must be a path.")
    if config_path is not None:
        root = root_override.resolve() if root_override is not None else (effective_path.parent / configured_root).resolve()
    if not root.is_dir():
        raise ExportError(f"The source root does not exist or is not a directory: {root}")

    output_value = export_data.get("output_dir", "exports")
    if not isinstance(output_value, str):
        raise ExportError("`export.output_dir` must be a path.")
    output_dir = (root / output_value).resolve()
    _ensure_within(output_dir, root, "The output directory is outside the root.")

    excludes = tuple(dict.fromkeys(ALWAYS_EXCLUDED + _as_tuple(export_data.get("exclude"), "export.exclude")))
    source_languages = _source_languages(export_data.get("source_languages"))
    default_profile = export_data.get("default_profile", "")
    if not isinstance(default_profile, str):
        raise ExportError("`export.default_profile` must be a string.")

    profiles = _profile_table(data.get("profiles", {}), source)
    personal_profile_names: set[str] = set()
    personal_path = _personal_profiles_path(effective_path)
    if personal_path.exists():
        try:
            personal_data = tomllib.loads(personal_path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise ExportError(f"Invalid TOML in {personal_path}: {exc}") from exc
        personal_profiles = _profile_table(
            personal_data.get("profiles", {}),
            str(personal_path),
        )
        profiles.update(personal_profiles)
        personal_profile_names.update(personal_profiles)
    if default_profile and default_profile not in profiles:
        raise ExportError(f"Default profile `{default_profile}` does not exist.")
    return ProjectConfig(
        effective_path,
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
            f"`{profile.name}` is a shared profile; choose another name for the personal profile."
        )
    profiles = {
        name: config.profiles[name]
        for name in config.personal_profile_names
        if name in config.profiles
    }
    profiles[profile.name] = profile
    lines = [
        "# Personal exporter profiles. This file should not be committed.",
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
            raise ExportError(f"Profile `{profile_name}` does not exist.") from exc
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
        index_excludes=config.excludes,
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
        raise ExportError("The export name does not contain any usable characters.")
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


def _is_excluded_directory(relative: str, patterns: Iterable[str]) -> bool:
    return _is_excluded(relative, patterns) or _is_excluded(
        f"{relative.rstrip('/')}/__descendant__",
        patterns,
    )


def _glob_variants(pattern: str) -> tuple[str, ...]:
    variants = {pattern}
    pending = [pattern]
    while pending:
        current = pending.pop()
        start = 0
        while True:
            position = current.find("**/", start)
            if position < 0:
                break
            variant = current[:position] + current[position + 3 :]
            if variant not in variants:
                variants.add(variant)
                pending.append(variant)
            start = position + 1
    return tuple(variants)


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
        self.relative_by_path: dict[Path, str] = {}
        candidates: list[Path] = []
        for directory, child_directories, filenames in os.walk(
            self.root,
            topdown=True,
            followlinks=False,
        ):
            directory_path = Path(directory)
            relative_directory = directory_path.relative_to(self.root).as_posix()
            child_directories[:] = sorted(
                (
                    name
                    for name in child_directories
                    if not _is_excluded_directory(
                        (
                            f"{relative_directory}/{name}"
                            if relative_directory != "."
                            else name
                        ),
                        excludes,
                    )
                ),
                key=str.casefold,
            )
            for filename in filenames:
                candidate = directory_path / filename
                if _is_exportable_source(candidate, source_languages):
                    candidates.append(candidate)

        for path in sorted(
            candidates,
            key=lambda item: item.relative_to(self.root).as_posix().casefold(),
        ):
            _ensure_within(path, self.root, f"An indexed source is outside the root: {path}")
            relative = path.relative_to(self.root).as_posix()
            if _is_excluded(relative, excludes):
                continue
            resolved = path.resolve()
            self.files.append(resolved)
            self.relative_by_path[resolved] = relative
            self.by_relative[relative.casefold()] = resolved
            no_suffix = Path(relative).with_suffix("").as_posix().casefold()
            if path.suffix.casefold() == ".md" or no_suffix not in self.by_relative:
                self.by_relative[no_suffix] = resolved
            self.by_stem[path.stem.casefold()].append(resolved)

    def relative(self, path: Path) -> str:
        known = self.relative_by_path.get(path)
        if known is not None:
            return known
        resolved = path.resolve()
        try:
            return self.relative_by_path[resolved]
        except KeyError:
            return _relative(resolved, self.root)

    def matches(self, entry: str) -> list[Path]:
        normalized = entry.replace("\\", "/").strip().strip("/")
        if normalized in {"", "."}:
            return list(self.files)

        candidate = self.root / normalized
        if candidate.is_dir():
            prefix = normalized.casefold().rstrip("/") + "/"
            return [
                path
                for path in self.files
                if self.relative_by_path[path].casefold().startswith(prefix)
            ]
        if candidate.is_file():
            resolved = candidate.resolve()
            return [resolved] if resolved in self.relative_by_path else []

        exact = self.by_relative.get(normalized.casefold())
        if exact is not None and not any(character in normalized for character in "*?["):
            return [exact]
        return [
            path
            for path in self.files
            if any(
                PurePosixPath(self.relative_by_path[path]).match(pattern)
                for pattern in _glob_variants(normalized)
            )
        ]

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
            return None, f"Ambiguous target `{target}`: {names}"
        return None, f"Document `{target}` does not exist"


def select_paths(options: ExportOptions, index: VaultIndex) -> list[Path]:
    if index.root != options.root.resolve():
        raise ExportError("The index does not belong to the export root.")
    selected: list[Path] = []
    seen: set[Path] = set()
    for entry in options.includes:
        entry = entry.replace("\\", "/").strip()
        if not entry:
            continue
        _reject_unsafe_selection(entry)
        for path in index.matches(entry):
            relative = index.relative(path)
            if _is_excluded(relative, options.excludes):
                continue
            if path not in seen:
                seen.add(path)
                selected.append(path)
    if not selected:
        raise ExportError("The selection contains no exportable sources.")
    return selected


def _reject_unsafe_selection(entry: str) -> None:
    path = Path(entry)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ExportError(f"Unsafe selection or path outside the root: {entry}")


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
    return f"markdown-export-doc-{_slug(relative)}-{digest}"


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


def _inline_code_segments(line: str) -> Iterator[tuple[str, bool]]:
    """Split a line without interpreting references inside inline code spans."""
    cursor = 0
    plain_start = 0
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue
        opening_end = cursor + 1
        while opening_end < len(line) and line[opening_end] == "`":
            opening_end += 1
        width = opening_end - cursor
        search = opening_end
        closing_start = -1
        while search < len(line):
            candidate = line.find("`" * width, search)
            if candidate < 0:
                break
            before_is_tick = candidate > 0 and line[candidate - 1] == "`"
            after = candidate + width
            after_is_tick = after < len(line) and line[after] == "`"
            if not before_is_tick and not after_is_tick:
                closing_start = candidate
                break
            search = candidate + 1
        if closing_start < 0:
            cursor = opening_end
            continue
        if plain_start < cursor:
            yield line[plain_start:cursor], False
        closing_end = closing_start + width
        yield line[cursor:closing_end], True
        cursor = closing_end
        plain_start = cursor
    if plain_start < len(line):
        yield line[plain_start:], False


def _iter_local_references(
    text: str,
    index: VaultIndex,
) -> Iterator[tuple[str, str | None, str, bool]]:
    for line, fenced in _fenced_lines(text):
        if fenced:
            continue
        for segment, inline_code in _inline_code_segments(line):
            if inline_code:
                continue
            for match in WIKILINK_RE.finditer(segment):
                target, heading, label = _split_wikilink(match.group(2))
                yield target, heading, label, bool(match.group(1))
            for match in MARKDOWN_LINK_RE.finditer(segment):
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
                diagnostics.add("warning", "asset-not-supported", f"Embed or attachment not exported: `{target}`", _relative(source, options.root), target)
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
    # Markdown links are processed before wikilinks so a link produced while
    # expanding a wikilink is not processed again as source text.
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
            diagnostics.add("warning", "asset-not-supported", f"Attachment not exported: `{target}`", source.relative, target)
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

    def wiki_replace(match: re.Match[str]) -> str:
        embedded = bool(match.group(1))
        target, heading, label = _split_wikilink(match.group(2))
        if embedded:
            diagnostics.add("warning", "asset-not-supported", f"Embed or attachment not exported: `{target}`", source.relative, target)
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

    def rewrite_text(text: str) -> str:
        rewritten = MARKDOWN_LINK_RE.sub(markdown_replace, text)
        return WIKILINK_RE.sub(wiki_replace, rewritten)

    return "".join(
        segment if inline_code else rewrite_text(segment)
        for segment, inline_code in _inline_code_segments(line)
    )


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
        output.extend([f"> Source: `{document.relative}`", ""])

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
        parts.extend(["## References not included", ""])
        for label, target in omitted:
            parts.append(f"- {label}: `{target}`")
        parts.append("")
    if diagnostics.items:
        parts.extend(["## Export diagnostics", ""])
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
            diagnostics.add("warning", "oversized-document", f"`{relative}` exceeds max_chars={max_chars} on its own.", relative)
        if current and current_size + len(content) > max_chars:
            groups.append(current)
            current = []
            current_size = 0
        current.append((relative, content))
        current_size += len(content)
    if current:
        groups.append(current)
    return groups


def build_export(
    options: ExportOptions,
    index: VaultIndex | None = None,
) -> ExportResult:
    options = replace(options, root=options.root.resolve(), output_dir=options.output_dir.resolve())
    _ensure_within(options.output_dir, options.root, "The output directory is outside the root.")
    if options.max_chars < 0:
        raise ExportError("`max_chars` cannot be negative.")

    # Profile exclusions prevent selecting or following a document, but do not
    # make it nonexistent: it can still be reported as an omitted reference.
    # Only internal directories that are never content are excluded from the
    # resolution index.
    if index is None:
        index = VaultIndex(
            options.root,
            options.index_excludes,
            options.source_languages,
        )
    elif (
        index.root != options.root
        or index.source_languages != options.source_languages
    ):
        raise ExportError("The index is not compatible with this export.")
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
        raise ExportError(f"Strict mode rejected the export: {messages}")

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
        _ensure_within(base_output, options.root, "The output is outside the project root.")
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
