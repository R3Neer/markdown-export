from __future__ import annotations

from pathlib import Path

from markdown_export.core import Profile, load_config, save_personal_profile


def test_builtin_configuration_uses_requested_root(tmp_path: Path) -> None:
    config = load_config(None, tmp_path)

    assert config.root == tmp_path.resolve()
    assert config.default_profile == "markdown"
    assert config.profiles["markdown"].include == (".",)


def test_external_configuration_resolves_relative_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "vault").mkdir()
    config_path = project / "markdown-export.toml"
    config_path.write_text(
        '[export]\nroot = "vault"\noutput_dir = "exports"\ndefault_profile = "all"\n\n'
        '[profiles.all]\ntitle = "All"\ninclude = ["**/*.md"]\n',
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.root == (project / "vault").resolve()
    assert config.output_dir == (project / "vault" / "exports").resolve()


def test_builtin_configuration_saves_personal_profiles_next_to_root(tmp_path: Path) -> None:
    config = load_config(None, tmp_path)

    target = save_personal_profile(
        config,
        Profile(name="personal", title="Personal", include=("notes/*.md",)),
    )

    assert target == tmp_path / ".markdown-export.local.toml"
