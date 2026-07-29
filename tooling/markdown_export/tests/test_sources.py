from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tooling.markdown_export.core import (
    ALWAYS_EXCLUDED,
    ExportOptions,
    build_export,
)


SOURCE_LANGUAGES = ((".asdl", "asdl"), (".ebnf", "ebnf"))


class ConfiguredSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

    def options(self, *includes: str, **changes) -> ExportOptions:
        values = {
            "root": self.root,
            "output_dir": self.root / "exports",
            "name": "test",
            "title": "Test",
            "includes": tuple(includes),
            "excludes": ALWAYS_EXCLUDED,
            "source_languages": SOURCE_LANGUAGES,
        }
        values.update(changes)
        return ExportOptions(**values)

    def test_configured_sources_are_fenced_and_linkable_documents(self) -> None:
        self.write("index.md", "# Índice\n[Gramática](grammar/mud.ebnf)\n")
        self.write("grammar/mud.ebnf", 'document = "```";\n')

        result = build_export(
            self.options("index.md", follow_links=True, strict_links=True)
        )

        self.assertEqual(result.explicit_documents, ("index.md",))
        self.assertEqual(result.dependency_documents, ("grammar/mud.ebnf",))
        self.assertRegex(result.parts[0].content, r"\[Gramática\]\(#mud-doc-")
        self.assertIn("## mud.ebnf", result.parts[0].content)
        self.assertIn("````ebnf\ndocument = \"```\";\n````", result.parts[0].content)
        self.assertFalse(result.diagnostics)

    def test_directory_selection_includes_all_configured_source_languages(self) -> None:
        self.write("specification/chapter.md", "# Capítulo\n")
        self.write("specification/grammar.ebnf", "rule = value;\n")
        self.write("specification/actions.asdl", "action Example\n")
        self.write("specification/ignored.txt", "no\n")

        result = build_export(self.options("specification"))

        self.assertEqual(
            result.explicit_documents,
            (
                "specification/actions.asdl",
                "specification/chapter.md",
                "specification/grammar.ebnf",
            ),
        )
        self.assertIn("```asdl\naction Example\n```", result.parts[0].content)
        self.assertIn("```ebnf\nrule = value;\n```", result.parts[0].content)
        self.assertNotIn("ignored.txt", result.parts[0].content)


if __name__ == "__main__":
    unittest.main()
