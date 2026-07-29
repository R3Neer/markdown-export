from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import ExportError, build_export, load_config, options_from_profile, write_export


DEFAULT_CONFIG = Path(__file__).with_name("profiles.toml")


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, help="Raíz de la bóveda (sobrescribe el TOML).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Archivo TOML de configuración.")


def _export_arguments(parser: argparse.ArgumentParser) -> None:
    _common_arguments(parser)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--profile", help="Perfil configurado.")
    source.add_argument("--files", nargs="+", metavar="RUTA", help="Archivos, carpetas o patrones relativos.")
    parser.add_argument("--name", help="Nombre base del export.")
    parser.add_argument("--output", type=Path, help="Ruta de salida relativa a la raíz.")
    parser.add_argument("--follow-links", action=argparse.BooleanOptionalAction, default=None)
    frontmatter = parser.add_mutually_exclusive_group()
    frontmatter.add_argument("--strip-frontmatter", dest="strip_frontmatter", action="store_true")
    frontmatter.add_argument("--keep-frontmatter", dest="strip_frontmatter", action="store_false")
    parser.set_defaults(strip_frontmatter=None)
    parser.add_argument("--source-markers", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--strict-links", action="store_true", default=None)
    parser.add_argument("--max-chars", type=int)
    parser.add_argument("--timestamp", action="store_true")
    parser.add_argument(
        "--zip-tree",
        action="store_true",
        help="Crea un ZIP con cada fuente en su ruta relativa original.",
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tooling.markdown_export",
        description="Combina Markdown de una bóveda en documentos portables.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profiles = commands.add_parser("list-profiles", help="Enumera los perfiles disponibles.")
    _common_arguments(profiles)

    export = commands.add_parser("export", help="Genera un export Markdown.")
    _export_arguments(export)

    serve = commands.add_parser("serve", help="Abre la interfaz web local.")
    _common_arguments(serve)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument(
        "--ready-json",
        action="store_true",
        help="Emite un único mensaje JSON de disponibilidad para integraciones.",
    )
    return parser


def _configuration(args: argparse.Namespace):
    return load_config(args.config, args.root)


def _run_export(args: argparse.Namespace) -> int:
    config = _configuration(args)
    profile_name = args.profile
    if args.files is None and profile_name is None:
        profile_name = config.default_profile
    output = args.output
    if output is not None and not output.is_absolute():
        output = config.root / output
    options = options_from_profile(
        config,
        profile_name or "",
        includes=args.files,
        name=args.name,
        output=output,
        follow_links=args.follow_links,
        strip_frontmatter=args.strip_frontmatter,
        source_markers=args.source_markers,
        strict_links=args.strict_links,
        max_chars=args.max_chars,
        timestamp=args.timestamp,
        zip_tree=args.zip_tree,
    )
    result = build_export(options)
    targets = write_export(options, result)
    print(f"Exportados {len(result.explicit_documents) + len(result.dependency_documents)} documentos.")
    for target in targets:
        print(target)
    for diagnostic in result.diagnostics:
        location = f" [{diagnostic.source}]" if diagnostic.source else ""
        print(f"{diagnostic.level.upper()} {diagnostic.code}{location}: {diagnostic.message}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list-profiles":
            config = _configuration(args)
            for name, profile in config.profiles.items():
                default = " (predeterminado)" if name == config.default_profile else ""
                print(f"{name}{default}: {profile.title}")
            return 0
        if args.command == "export":
            return _run_export(args)
        if args.command == "serve":
            from .web import serve

            config = _configuration(args)
            serve(
                config,
                port=args.port,
                open_browser=not args.no_browser,
                ready_json=args.ready_json,
            )
            return 0
    except (ExportError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("Comando desconocido.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
