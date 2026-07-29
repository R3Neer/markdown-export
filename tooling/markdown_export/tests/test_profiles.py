from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tooling.markdown_export.core import (
    VaultIndex,
    load_config,
    options_from_profile,
    select_paths,
)


class BundledProfileTests(unittest.TestCase):
    def test_language_profile_grows_with_normative_directories(self) -> None:
        config_path = Path(__file__).parents[1] / "profiles.toml"
        bundled = load_config(config_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = {
                "especificacion/README.md",
                "especificacion/05-texto-fuente.md",
                "especificacion/gramatica/mud.ebnf",
                "especificacion/asdl/acciones.asdl",
                "notas/01-vision-y-alcance.md",
                "notas/02-modelo-del-lenguaje.md",
                "notas/03-semantica-de-ejecucion.md",
                "notas/preguntas/README.md",
                "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
                "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
                "notas/10-registro-de-decisiones.md",
                "notas/12-destruccion-colecciones-y-grafo-activo.md",
                "notas/decisiones/ADR-054-lenguaje.md",
                "notas/decisiones/ADR-055-nueva-decision.md",
                "notas/decisiones/ADR-051-grafo-semantico-e-ir-reconstruibles.md",
                "notas/decisiones/ADR-052-pipeline-materializadores-y-conformidad.md",
                "notas/decisiones/ADR-053-operador-semantico-y-flujo-de-autoria.md",
                "notas/07-plan-de-formalizacion.md",
                "tooling/README.md",
            }
            for relative in documents:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {path.stem}\n", encoding="utf-8")

            config = replace(
                bundled,
                root=root,
                output_dir=root / "exports",
            )
            options = options_from_profile(config, "language")
            index = VaultIndex(root, options.excludes, options.source_languages)
            selected = {
                path.relative_to(root).as_posix()
                for path in select_paths(options, index)
            }

        self.assertIn("especificacion/05-texto-fuente.md", selected)
        self.assertIn("especificacion/gramatica/mud.ebnf", selected)
        self.assertIn("especificacion/asdl/acciones.asdl", selected)
        self.assertIn("notas/decisiones/ADR-055-nueva-decision.md", selected)
        self.assertIn("notas/preguntas/README.md", selected)
        self.assertIn(
            "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
            selected,
        )
        self.assertNotIn(
            "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
            selected,
        )
        self.assertNotIn(
            "notas/decisiones/ADR-051-grafo-semantico-e-ir-reconstruibles.md",
            selected,
        )
        self.assertNotIn(
            "notas/decisiones/ADR-052-pipeline-materializadores-y-conformidad.md",
            selected,
        )
        self.assertNotIn(
            "notas/decisiones/ADR-053-operador-semantico-y-flujo-de-autoria.md",
            selected,
        )
        self.assertNotIn("notas/07-plan-de-formalizacion.md", selected)
        self.assertNotIn("tooling/README.md", selected)

    def test_decisions_profile_preserves_the_complete_question_history(self) -> None:
        config_path = Path(__file__).parents[1] / "profiles.toml"
        bundled = load_config(config_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = {
                "notas/10-registro-de-decisiones.md",
                "notas/decisiones/ADR-062-lenguaje.md",
                "notas/preguntas/README.md",
                "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
                "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
                "especificacion/README.md",
            }
            for relative in documents:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {path.stem}\n", encoding="utf-8")

            config = replace(bundled, root=root, output_dir=root / "exports")
            options = options_from_profile(config, "decisions")
            index = VaultIndex(root, options.excludes, options.source_languages)
            selected = {
                path.relative_to(root).as_posix()
                for path in select_paths(options, index)
            }

        self.assertIn("notas/preguntas/README.md", selected)
        self.assertIn(
            "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
            selected,
        )
        self.assertIn(
            "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
            selected,
        )
        self.assertNotIn("especificacion/README.md", selected)

    def test_current_profile_excludes_closed_questions(self) -> None:
        config_path = Path(__file__).parents[1] / "profiles.toml"
        bundled = load_config(config_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = {
                "notas/preguntas/README.md",
                "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
                "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
                "exports/current.md",
                "tooling/example/node_modules/dependency/README.md",
            }
            for relative in documents:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {path.stem}\n", encoding="utf-8")

            config = replace(bundled, root=root, output_dir=root / "exports")
            options = options_from_profile(config, "current")
            index = VaultIndex(root, options.excludes, options.source_languages)
            selected = {
                path.relative_to(root).as_posix()
                for path in select_paths(options, index)
            }

        self.assertIn("notas/preguntas/README.md", selected)
        self.assertIn(
            "notas/preguntas/Q-002-modelo-exacto-de-efectos-secuenciales-y-simultaneos.md",
            selected,
        )
        self.assertNotIn(
            "notas/preguntas/Q-001-gramatica-y-saltos-de-linea.md",
            selected,
        )
        self.assertNotIn("exports/current.md", selected)
        self.assertNotIn(
            "tooling/example/node_modules/dependency/README.md",
            selected,
        )


if __name__ == "__main__":
    unittest.main()
