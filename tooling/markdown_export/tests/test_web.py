from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tooling.markdown_export.core import ProjectConfig, Profile
from tooling.markdown_export.web import create_server


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "a.md").write_text("# A\n[[b]]\n", encoding="utf-8")
        (self.root / "b.md").write_text("# B\n", encoding="utf-8")
        profile = Profile("test", "Test", ("a.md",))
        self.config = ProjectConfig(
            self.root / "profiles.toml",
            self.root,
            self.root / "exports",
            (".git/**", ".obsidian/**", ".trash/**", "exports/**"),
            "test",
            {"test": profile},
        )
        self.server, self.token = create_server(self.config, port=0, token="known-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, path: str, *, payload=None, token: str | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Export-Token"] = token
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def payload(self):
        return {
            "profile": "test",
            "files": ["a.md"],
            "name": "web-test",
            "follow_links": True,
            "strip_frontmatter": True,
            "source_markers": True,
            "strict_links": False,
            "max_chars": 0,
        }

    def test_tree_preview_and_export(self) -> None:
        status, tree = self.request("/api/tree")
        self.assertEqual(status, 200)
        self.assertEqual(tree["files"], ["a.md", "b.md"])
        status, preview = self.request("/api/preview", payload=self.payload(), token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(preview["dependencies"], ["b.md"])
        status, exported = self.request("/api/export", payload=self.payload(), token=self.token)
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "exports" / "web-test.md").exists())
        self.assertEqual(len(exported["outputs"]), 1)

    def test_invalid_token_and_traversal_are_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as token_error:
            self.request("/api/preview", payload=self.payload(), token="wrong")
        self.assertEqual(token_error.exception.code, 403)
        token_error.exception.close()
        payload = self.payload()
        payload["files"] = ["../outside.md"]
        with self.assertRaises(urllib.error.HTTPError) as traversal_error:
            self.request("/api/preview", payload=payload, token=self.token)
        self.assertEqual(traversal_error.exception.code, 400)
        traversal_error.exception.close()


if __name__ == "__main__":
    unittest.main()
