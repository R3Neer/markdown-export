from __future__ import annotations

from pathlib import Path

from markdown_export.cli import main


def test_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "0.1.1\n"


def test_command_help_does_not_read_configuration(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.toml"

    assert main(["export", "--config", str(missing), "--help"]) == 0

    captured = capsys.readouterr()
    assert "Export selected sources" in captured.out
    assert captured.err == ""


def test_plain_ascii_command_help(capsys) -> None:
    assert main(["export", "--help", "--colour", "never", "--ascii"]) == 0

    captured = capsys.readouterr()
    assert "EXPORT" in captured.out
    assert "--name NAME" in captured.out
    assert "\x1b[" not in captured.out


def test_global_help_documents_presentation_options(capsys) -> None:
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "GLOBAL OPTIONS" in captured.out
    assert "--version" in captured.out
    assert "--colour auto|always|never" in captured.out


def test_default_export_uses_builtin_profile(tmp_path: Path, capsys) -> None:
    (tmp_path / "hello.md").write_text("# Hello\n", encoding="utf-8")

    assert main(["--colour", "never", "export", "--root", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert "Exported 1 document(s)." in captured.out
    assert (tmp_path / "exports" / "markdown.md").is_file()
