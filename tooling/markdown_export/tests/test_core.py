from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tooling.markdown_export.core as core
from tooling.markdown_export.core import (
    ALWAYS_EXCLUDED,
    ExportError,
    ExportOptions,
    build_export,
    strip_frontmatter,
    write_export,
)


class VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "exports"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str, *, bom: bool = False) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoding = "utf-8-sig" if bom else "utf-8"
        path.write_text(content, encoding=encoding, newline="")
        return path

    def options(self, *includes: str, **changes) -> ExportOptions:
        values = {
            "root": self.root,
            "output_dir": self.output,
            "name": "test",
            "title": "Test",
            "includes": tuple(includes),
            "excludes": ALWAYS_EXCLUDED,
        }
        values.update(changes)
        return ExportOptions(**values)


class MarkdownTransformationTests(VaultCase):
    def test_frontmatter_bom_lf_headings_and_code_are_preserved_correctly(self) -> None:
        self.write(
            "doc.md",
            "---\r\ntags: [x]\r\n---\r\n# Título\r\n## Sección\r\n```mud\r\n## no es título\r\n[[NoExiste]]\r\n```\r\n",
            bom=True,
        )
        result = build_export(self.options("doc.md"))
        content = result.parts[0].content
        self.assertNotIn("tags: [x]", content)
        self.assertNotIn("\r", content)
        self.assertIn("## Título", content)
        self.assertIn("### Sección", content)
        self.assertIn("## no es título", content)
        self.assertIn("[[NoExiste]]", content)
        self.assertFalse(result.diagnostics)

    def test_unclosed_frontmatter_is_not_removed(self) -> None:
        self.assertEqual(strip_frontmatter("---\na: 1\ntexto"), "---\na: 1\ntexto")

    def test_wikilinks_alias_heading_and_markdown_links(self) -> None:
        self.write(
            "a/uno.md",
            "# Uno\n[[../b/dos#Detalle|véase dos]]\n[dos también](../b/dos.md#Detalle)\n"
            "[externo](https://example.com)\n",
        )
        self.write("b/dos.md", "# Dos\n## Detalle\nTexto\n")
        result = build_export(self.options("a/uno.md", "b/dos.md"))
        content = result.parts[0].content
        self.assertNotIn("[[", content)
        self.assertRegex(content, r"\[véase dos\]\(#mud-doc-.*-detalle\)")
        self.assertRegex(content, r"\[dos también\]\(#mud-doc-.*-detalle\)")
        self.assertIn("[externo](https://example.com)", content)

    def test_extensionless_and_angle_bracket_markdown_links_are_resolved(self) -> None:
        self.write("a.md", "# A\n[sin extensión](folder/b) [con espacio](<folder/con espacio.md>)\n")
        self.write("folder/b.md", "# B\n")
        self.write("folder/con espacio.md", "# Espacio\n")
        result = build_export(self.options("a.md", "folder/*.md"))
        self.assertRegex(result.parts[0].content, r"\[sin extensión\]\(#mud-doc-")
        self.assertRegex(result.parts[0].content, r"\[con espacio\]\(#mud-doc-")

    def test_attachments_warn_and_strict_mode_rejects_them(self) -> None:
        self.write("a.md", "# A\n[manual](manual.pdf) ![imagen](image.png)\n")
        loose = build_export(self.options("a.md"))
        self.assertEqual([item.code for item in loose.diagnostics], ["asset-not-supported", "asset-not-supported"])
        self.assertNotIn("manual.pdf", loose.parts[0].content.split("## Diagnósticos")[0])
        with self.assertRaisesRegex(ExportError, "modo estricto"):
            build_export(self.options("a.md", strict_links=True))

    def test_links_in_headings_are_also_rewritten(self) -> None:
        self.write("a.md", "# A\n## Véase [[b|B]]\n")
        self.write("b.md", "# B\n")
        result = build_export(self.options("a.md", "b.md"))
        outside_fences = [
            line for line, fenced in core._fenced_lines(result.parts[0].content) if not fenced
        ]
        self.assertNotIn("[[", "\n".join(outside_fences))
        self.assertRegex(result.parts[0].content, r"### Véase \[B\]\(#mud-doc-")

    def test_omitted_reference_is_plain_text_and_appears_in_appendix(self) -> None:
        self.write("a.md", "# A\n[[b|B visible]]\n")
        self.write("b.md", "# B\n")
        result = build_export(self.options("a.md"))
        self.assertIn("B visible", result.parts[0].content)
        self.assertIn("## Referencias no incluidas", result.parts[0].content)
        self.assertEqual(result.omitted_references, (("B visible", "b.md"),))

    def test_ambiguous_and_missing_links_warn_or_fail_in_strict_mode(self) -> None:
        self.write("a.md", "# A\n[[Duplicado]] [[Ausente]]\n")
        self.write("x/Duplicado.md", "# X\n")
        self.write("y/Duplicado.md", "# Y\n")
        loose = build_export(self.options("a.md"))
        self.assertEqual([item.code for item in loose.diagnostics], ["unresolved-link", "unresolved-link"])
        outside_fences = [
            line for line, fenced in core._fenced_lines(loose.parts[0].content) if not fenced
        ]
        self.assertNotIn("[[", "\n".join(outside_fences))
        with self.assertRaisesRegex(ExportError, "modo estricto"):
            build_export(self.options("a.md", strict_links=True))


class SelectionAndDependencyTests(VaultCase):
    def test_recursive_following_handles_cycles_once_in_discovery_order(self) -> None:
        self.write("a.md", "# A\n[[c]] [[b]]\n")
        self.write("b.md", "# B\n[[a]]\n")
        self.write("c.md", "# C\n[[b]]\n")
        result = build_export(self.options("a.md", follow_links=True))
        self.assertEqual(result.explicit_documents, ("a.md",))
        self.assertEqual(result.dependency_documents, ("c.md", "b.md"))

    def test_explicit_order_precedes_dependencies(self) -> None:
        self.write("z.md", "# Z\n[[dependency]]\n")
        self.write("a.md", "# A\n")
        self.write("dependency.md", "# Dep\n")
        result = build_export(self.options("z.md", "a.md", follow_links=True))
        self.assertEqual(result.explicit_documents, ("z.md", "a.md"))
        self.assertEqual(result.dependency_documents, ("dependency.md",))
        self.assertEqual(result.parts[0].documents, ("z.md", "a.md", "dependency.md"))

    def test_always_excluded_directories_are_ignored(self) -> None:
        self.write("ok.md", "# OK\n")
        self.write(".obsidian/private.md", "# No\n")
        result = build_export(self.options("**/*.md"))
        self.assertEqual(result.explicit_documents, ("ok.md",))

    def test_profile_excluded_document_can_be_reported_as_omitted(self) -> None:
        self.write("active.md", "# Activo\n[[archive/old]]\n")
        self.write("archive/old.md", "# Antiguo\n")
        options = self.options("**/*.md", excludes=ALWAYS_EXCLUDED + ("archive/**",))
        result = build_export(options)
        self.assertEqual(result.explicit_documents, ("active.md",))
        self.assertEqual(result.omitted_references, (("old", "archive/old.md"),))
        self.assertFalse(result.diagnostics)

    def test_traversal_and_absolute_selection_are_rejected(self) -> None:
        self.write("a.md", "# A\n")
        with self.assertRaisesRegex(ExportError, "insegura"):
            build_export(self.options("../fuera.md"))
        with self.assertRaisesRegex(ExportError, "insegura"):
            build_export(self.options(str((self.root / "a.md").resolve())))


class OutputTests(VaultCase):
    def test_size_split_never_cuts_documents(self) -> None:
        self.write("a.md", "# A\n" + "a" * 80)
        self.write("b.md", "# B\n" + "b" * 80)
        result = build_export(self.options("a.md", "b.md", max_chars=100))
        self.assertEqual(len(result.parts), 2)
        self.assertEqual(result.parts[0].documents, ("a.md",))
        self.assertEqual(result.parts[1].documents, ("b.md",))

    def test_oversized_single_document_is_kept_and_warned(self) -> None:
        self.write("a.md", "# A\n" + "a" * 200)
        result = build_export(self.options("a.md", max_chars=20))
        self.assertEqual(len(result.parts), 1)
        self.assertIn("oversized-document", [item.code for item in result.diagnostics])

    def test_same_input_produces_identical_bytes_and_atomic_write_leaves_no_temp(self) -> None:
        self.write("a.md", "# A\nTexto\n")
        options = self.options("a.md")
        first = build_export(options)
        targets = write_export(options, first)
        first_bytes = targets[0].read_bytes()
        second = build_export(options)
        write_export(options, second)
        self.assertEqual(first_bytes, targets[0].read_bytes())
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_output_outside_root_is_rejected_without_partial_file(self) -> None:
        self.write("a.md", "# A\n")
        outside = self.root.parent / "forbidden-export.md"
        options = self.options("a.md", output=outside)
        result = build_export(options)
        with self.assertRaisesRegex(ExportError, "fuera"):
            write_export(options, result)
        self.assertFalse(outside.exists())

    def test_atomic_write_cleans_temporary_file_after_replace_error(self) -> None:
        target = self.output / "failed.md"
        with mock.patch("tooling.markdown_export.core.os.replace", side_effect=OSError("fallo simulado")):
            with self.assertRaisesRegex(OSError, "fallo simulado"):
                core._atomic_write(target, "contenido")
        self.assertFalse(target.exists())
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_multipart_publish_rolls_back_parts_after_later_error(self) -> None:
        self.write("a.md", "# A\n" + "a" * 80)
        self.write("b.md", "# B\n" + "b" * 80)
        options = self.options("a.md", "b.md", max_chars=100)
        result = build_export(options)
        real_replace = core.os.replace
        calls = 0

        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("segunda parte")
            return real_replace(source, target)

        with mock.patch("tooling.markdown_export.core.os.replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "segunda parte"):
                write_export(options, result)
        self.assertEqual(list(self.output.glob("test.part-*.md")), [])
        self.assertEqual(list(self.output.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
