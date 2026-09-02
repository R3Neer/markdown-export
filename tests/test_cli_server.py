from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path


class ServeCliTests(unittest.TestCase):
    def test_ready_json_and_health_over_dynamic_port(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as root:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "markdown_export",
                    "serve",
                    "--root",
                    root,
                    "--port",
                    "0",
                    "--no-browser",
                    "--ready-json",
                ],
                cwd=repository,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            lines: queue.Queue[str] = queue.Queue()

            def read_ready_line() -> None:
                assert process.stdout is not None
                lines.put(process.stdout.readline())

            reader = threading.Thread(target=read_ready_line, daemon=True)
            reader.start()
            try:
                line = lines.get(timeout=5)
                self.assertNotIn("\x1b[", line)
                self.assertEqual(line.count("\n"), 1)
                ready = json.loads(line)
                self.assertEqual(ready["event"], "ready")
                self.assertEqual(ready["protocol_version"], 1)
                self.assertEqual(Path(ready["root"]), Path(root))
                with urllib.request.urlopen(
                    ready["url"] + "api/health",
                    timeout=3,
                ) as response:
                    health = json.loads(response.read())
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["protocol_version"], 1)
                self.assertEqual(Path(health["root"]), Path(root))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                reader.join(timeout=1)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
