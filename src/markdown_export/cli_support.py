from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NoReturn

from r3_cli import CliError, ConsoleUI, HelpCatalogue, add_output_arguments, resolve_help_request


@dataclass(frozen=True)
class ParsedCLI:
    arguments: argparse.Namespace | None
    ui: ConsoleUI
    exit_code: int | None = None


class ExportArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise CliError(
            "The command-line arguments are invalid.",
            code="MarkdownExport.Cli.InvalidArguments",
            details=message,
            hint=f"{self.prog} --help",
        )


def _presentation(argv: Sequence[str]) -> tuple[str, bool]:
    colour = "auto"
    ascii_output = "--ascii" in argv
    for index, value in enumerate(argv):
        if value.startswith("--colour="):
            candidate = value.partition("=")[2]
            if candidate in {"auto", "always", "never"}:
                colour = candidate
        elif value == "--colour" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in {"auto", "always", "never"}:
                colour = candidate
    if not ascii_output:
        encoding = sys.stdout.encoding or "utf-8"
        try:
            "═✓→•✗—".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            ascii_output = True
    return colour, ascii_output


def parse_cli(
    parser: argparse.ArgumentParser,
    catalogue: HelpCatalogue,
    argv: Sequence[str] | None = None,
    *,
    executable_commands: Iterable[str] | None = None,
) -> ParsedCLI:
    values = tuple(sys.argv[1:] if argv is None else argv)
    colour, ascii_output = _presentation(values)
    ui = ConsoleUI(colour=colour, ascii=ascii_output)
    try:
        catalogue.validate(executable_commands)
        request = resolve_help_request(values, catalogue)
        if request is not None:
            ui.help(catalogue, request.command)
            return ParsedCLI(None, ui, 0)
        return ParsedCLI(parser.parse_args(values), ui)
    except CliError as exc:
        ui.error(exc)
        return ParsedCLI(None, ui, exc.exit_code)


def add_presentation_arguments(parser: argparse.ArgumentParser) -> None:
    add_output_arguments(parser)


def failure(
    ui: ConsoleUI,
    message: str,
    *,
    code: str,
    details: str | None = None,
    hint: str | None = None,
    exit_code: int = 1,
) -> int:
    ui.error(CliError(message, code=code, details=details, hint=hint, exit_code=exit_code))
    return exit_code
