from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from gt_automation.process_runner import ProcessRunner


class ProcessRunnerTests(unittest.TestCase):
    def test_captures_output_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "task.log"
            runner = ProcessRunner()
            runner.start(
                command=[
                    sys.executable,
                    "-c",
                    "print('hello from worker')",
                ],
                cwd=Path(directory),
                log_path=log_path,
                label="test",
            )

            output = []
            deadline = time.monotonic() + 10
            exit_code = None
            while time.monotonic() < deadline:
                kind, payload = runner.events.get(timeout=1)
                if kind == "output":
                    output.append(str(payload))
                if kind == "exit":
                    exit_code = int(payload)
                    break

            self.assertEqual(exit_code, 0)
            self.assertIn("hello from worker", output)
            self.assertIn("hello from worker", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
