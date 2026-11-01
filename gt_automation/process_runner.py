"""Threaded cross-platform subprocess runner used by the desktop UI."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple


ProcessEvent = Tuple[str, object]


class ProcessRunner:
    def __init__(self) -> None:
        self.events: "queue.Queue[ProcessEvent]" = queue.Queue()
        self.process: Optional[subprocess.Popen[str]] = None
        self._log_file = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(
        self,
        command: List[str],
        cwd: Path,
        log_path: Path,
        label: str,
    ) -> None:
        if self.is_running:
            raise RuntimeError("已有任务正在运行")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except Exception:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            raise
        self.events.put(("started", label))
        self._thread = threading.Thread(
            target=self._reader,
            daemon=True,
            name="gt-process-reader",
        )
        self._thread.start()

    def _reader(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self.events.put(("error", "无法读取子进程输出"))
            return
        try:
            for line in process.stdout:
                text = line.rstrip("\n")
                self.events.put(("output", text))
                if self._log_file is not None:
                    self._log_file.write(line)
                    self._log_file.flush()
        finally:
            return_code = process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if self._log_file is not None:
                self._log_file.write(
                    "\n[exit_code]=%d\n" % return_code
                )
                self._log_file.flush()
                self._log_file.close()
                self._log_file = None
            self.process = None
            self.events.put(("exit", return_code))

    def stop(self, grace_seconds: float = 2.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=grace_seconds)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
