from __future__ import annotations

import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

import markdown_export.core as core
from markdown_export.core import (
    ALWAYS_EXCLUDED,
    ExportError,
    ExportOptions,
    VaultIndex,
    build_export,
    select_paths,
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
            "---\r\ntags: [x]\r\n---\r\n# Title\r\n## Section\r\n```text\r\n## not a title\r\n[[DoesNotExist]]\r\n```\r\n",
            bom=True,
        )
        result = build_export(self.options("doc.md"))
        content = result.parts[0].content
        self.assertNotIn("tags: [x]", content)
        self.assertNotIn("\r", content)
        self.assertIn("## Title", content)
        self.assertIn("### Section", content)
        self.assertIn("## not a title", content)
        self.assertIn("[[DoesNotExist]]", content)
        self.assertFalse(result.diagnostics)

    def test_unclosed_frontmatter_is_not_removed(self) -> None:
        self.assertEqual(strip_frontmatter("---\na: 1\ntext"), "---\na: 1\ntext")

    def test_wikilinks_alias_heading_and_markdown_links(self) -> None:
        self.write(
            "a/one.md",
            "# One\n[[../b/two#Detail|see two]]\n[two as well](../b/two.md#Detail)\n"
            "[external](https://example.com)\n",
        )
        self.write("b/two.md", "# Two\n## Detail\nText\n")
        result = build_export(self.options("a/one.md", "b/two.md"))
        content = result.parts[0].content
        self.assertNotIn("[[", content)
        self.assertRegex(content, r"\[see two\]\(#markdown-export-doc-.*-detail\)")
        self.assertRegex(content, r"\[two as well\]\(#markdown-export-doc-.*-detail\)")
        self.assertIn("[external](https://example.com)", content)

    def test_inline_code_is_not_treated_as_links(self) -> None:
        self.write(
            "a.md",
            "# A\n`[[DoesNotExist]]` [[b|B]] ``[false](manual.pdf) and `code` ``\n",
        )
        self.write("b.md", "# B\n")
        result = build_export(self.options("a.md", follow_links=True))
        content = result.parts[0].content
        self.assertIn("`[[DoesNotExist]]`", content)
        self.assertIn("``[false](manual.pdf) and `code` ``", content)
        self.assertRegex(content, r"\[B\]\(#markdown-export-doc-")
        self.assertEqual(result.dependency_documents, ("b.md",))
        self.assertFalse(result.diagnostics)

    def test_extensionless_and_angle_bracket_markdown_links_are_resolved(self) -> None:
        self.write("a.md", "# A\n[without extension](folder/b) [with space](<folder/with space.md>)\n")
        self.write("folder/b.md", "# B\n")
        self.write("folder/with space.md", "# Space\n")
        result = build_export(self.options("a.md", "folder/*.md"))
        self.assertRegex(result.parts[0].content, r"\[without extension\]\(#markdown-export-doc-")
        self.assertRegex(result.parts[0].content, r"\[with space\]\(#markdown-export-doc-")

    def test_attachments_warn_and_strict_mode_rejects_them(self) -> None:
        self.write("a.md", "# A\n[manual](manual.pdf) ![image](image.png)\n")
        loose = build_export(self.options("a.md"))
        self.assertEqual([item.code for item in loose.diagnostics], ["asset-not-supported", "asset-not-supported"])
        self.assertNotIn("manual.pdf", loose.parts[0].content.split("## Export diagnostics")[0])
        with self.assertRaisesRegex(ExportError, "Strict mode"):
            build_export(self.options("a.md", strict_links=True))

    def test_links_in_headings_are_also_rewritten(self) -> None:
        self.write("a.md", "# A\n## See [[b|B]]\n")
        self.write("b.md", "# B\n")
        result = build_export(self.options("a.md", "b.md"))
        outside_fences = [
            line for line, fenced in core._fenced_lines(result.parts[0].content) if not fenced
        ]
        self.assertNotIn("[[", "\n".join(outside_fences))
        self.assertRegex(result.parts[0].content, r"### See \[B\]\(#markdown-export-doc-")

    def test_omitted_reference_is_plain_text_and_appears_in_appendix(self) -> None:
        self.write("a.md", "# A\n[[b|B visible]]\n")
        self.write("b.md", "# B\n")
        result = build_export(self.options("a.md"))
        self.assertIn("B visible", result.parts[0].content)
        self.assertIn("## References not included", result.parts[0].content)
        self.assertEqual(result.omitted_references, (("B visible", "b.md"),))

    def test_ambiguous_and_missing_links_warn_or_fail_in_strict_mode(self) -> None:
        self.write("a.md", "# A\n[[Duplicate]] [[Missing]]\n")
        self.write("x/Duplicate.md", "# X\n")
        self.write("y/Duplicate.md", "# Y\n")
        loose = build_export(self.options("a.md"))
        self.assertEqual([item.code for item in loose.diagnostics], ["unresolved-link", "unresolved-link"])
        outside_fences = [
            line for line, fenced in core._fenced_lines(loose.parts[0].content) if not fenced
        ]
        self.assertNotIn("[[", "\n".join(outside_fences))
        with self.assertRaisesRegex(ExportError, "Strict mode"):
            build_export(self.options("a.md", strict_links=True))


class SelectionAndDependencyTests(VaultCase):
    def test_directory_and_glob_selection_reuse_the_catalog(self) -> None:
        self.write("docs/root.md", "# Root\n")
        self.write("docs/nested/child.md", "# Child\n")
        index = VaultIndex(self.root, ALWAYS_EXCLUDED)
        with mock.patch.object(
            Path,
            "rglob",
            side_effect=AssertionError("select_paths must not scan the disk"),
        ):
            directory = select_paths(self.options("docs"), index)
            globbed = select_paths(self.options("**/*.md"), index)
        expected = [
            self.root / "docs" / "nested" / "child.md",
            self.root / "docs" / "root.md",
        ]
        self.assertEqual(directory, expected)
        self.assertEqual(globbed, expected)

    def test_build_export_accepts_a_shared_catalog(self) -> None:
        self.write("a.md", "# A\n")
        options = self.options("a.md")
        index = VaultIndex(self.root, ALWAYS_EXCLUDED)
        with mock.patch(
            "markdown_export.core.VaultIndex",
            side_effect=AssertionError("build_export must not rebuild the index"),
        ):
            result = build_export(options, index)
        self.assertEqual(result.explicit_documents, ("a.md",))

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
        self.write("active.md", "# Active\n[[archive/old]]\n")
        self.write("archive/old.md", "# Old\n")
        options = self.options("**/*.md", excludes=ALWAYS_EXCLUDED + ("archive/**",))
        result = build_export(options)
        self.assertEqual(result.explicit_documents, ("active.md",))
        self.assertEqual(result.omitted_references, (("old", "archive/old.md"),))
        self.assertFalse(result.diagnostics)

    def test_traversal_and_absolute_selection_are_rejected(self) -> None:
        self.write("a.md", "# A\n")
        with self.assertRaisesRegex(ExportError, "Unsafe selection"):
            build_export(self.options("../fuera.md"))
        with self.assertRaisesRegex(ExportError, "Unsafe selection"):
            build_export(self.options(str((self.root / "a.md").resolve())))


class OutputTests(VaultCase):
    def test_zip_tree_preserves_relative_paths_and_separate_content(self) -> None:
        self.write("guide/start.md", "---\nprivate: true\n---\n# Inicio\n[[chapter/next]]\n")
        self.write("guide/chapter/next.md", "# Siguiente\n")
        options = self.options(
            "guide/start.md",
            follow_links=True,
            strip_frontmatter=True,
            zip_tree=True,
        )
        result = build_export(options)
        self.assertEqual(
            [part.filename for part in result.parts],
            ["guide/start.md", "guide/chapter/next.md"],
        )
        self.assertEqual(result.parts[0].content, "# Inicio\n[[chapter/next]]\n")
        self.assertNotIn("Fuente:", result.parts[0].content)
        target = write_export(options, result)[0]
        self.assertEqual(target, self.output / "test.zip")
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(
                archive.namelist(),
                ["guide/start.md", "guide/chapter/next.md"],
            )
            self.assertEqual(
                archive.read("guide/start.md").decode("utf-8"),
                "# Inicio\n[[chapter/next]]\n",
            )

    def test_size_partition_uses_document_boundaries(self) -> None:
        diagnostics = core._Diagnostics()
        groups = core._group_sections(
            [("ten.md", "x" * 10), ("five.md", "y" * 5)],
            12,
            diagnostics,
        )
        self.assertEqual(
            [[relative for relative, _content in group] for group in groups],
            [["ten.md"], ["five.md"]],
        )

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
        with self.assertRaisesRegex(ExportError, "outside"):
            write_export(options, result)
        self.assertFalse(outside.exists())

    def test_atomic_write_cleans_temporary_file_after_replace_error(self) -> None:
        target = self.output / "failed.md"
        with mock.patch("markdown_export.core.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaisesRegex(OSError, "simulated failure"):
                core._atomic_write(target, "contenido")
        self.assertFalse(target.exists())
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_multipart_is_a_deterministic_zip_and_cleans_superseded_outputs(self) -> None:
        self.write("a.md", "# A\n" + "a" * 80)
        self.write("b.md", "# B\n" + "b" * 80)
        options = replace(
            self.options("a.md", "b.md", max_chars=100),
            name="test.v1",
        )
        result = build_export(options)
        self.output.mkdir()
        (self.output / "test.v1.md").write_text("antiguo", encoding="utf-8")
        (self.output / "test.v1.part-003.md").write_text("resto", encoding="utf-8")
        target = write_export(options, result)[0]
        self.assertEqual(target, self.output / "test.v1.zip")
        self.assertFalse((self.output / "test.v1.md").exists())
        self.assertEqual(list(self.output.glob("test.v1.part-*.md")), [])
        first_bytes = target.read_bytes()
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), [part.filename for part in result.parts])
            self.assertEqual(
                archive.read(result.parts[0].filename).decode("utf-8"),
                result.parts[0].content,
            )
        write_export(options, result)
        self.assertEqual(target.read_bytes(), first_bytes)

        single_options = replace(self.options("a.md"), name="test.v1")
        single = build_export(single_options)
        single_target = write_export(single_options, single)[0]
        self.assertEqual(single_target, self.output / "test.v1.md")
        self.assertFalse(target.exists())

    def test_failed_zip_publish_leaves_no_partial_output(self) -> None:
        self.write("a.md", "# A\n" + "a" * 80)
        self.write("b.md", "# B\n" + "b" * 80)
        options = self.options("a.md", "b.md", max_chars=100)
        result = build_export(options)
        with mock.patch("markdown_export.core.os.replace", side_effect=OSError("ZIP failure")):
            with self.assertRaisesRegex(OSError, "ZIP failure"):
                write_export(options, result)
        self.assertFalse((self.output / "test.zip").exists())
        self.assertEqual(list(self.output.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
