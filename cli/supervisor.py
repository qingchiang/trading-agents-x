"""Foreground supervisor for the local Web and worker processes."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

from tradingagents.application.settings import AppSettings

STARTUP_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0
PROCESS_POLL_SECONDS = 0.1
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class _ProcessLog:
    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s")
        )

    def write(self, line: str) -> None:
        record = logging.LogRecord(
            name="tradingagents.supervisor",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=line.rstrip("\n"),
            args=(),
            exc_info=None,
        )
        self.handler.emit(record)

    def close(self) -> None:
        self.handler.close()


class LocalProcessSupervisor:
    """Start, monitor, and stop the local Web and worker child processes."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        log_level: str = "info",
        log_dir: Path | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        health_probe: Callable[[int], bool] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        output: Callable[[str], None] | None = None,
        startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
        shutdown_timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
    ):
        self.settings = settings
        self.log_level = log_level
        self.log_dir = log_dir.expanduser().resolve() if log_dir else None
        self.process_factory = process_factory
        self.health_probe = health_probe or _health_probe
        self.monotonic_clock = monotonic_clock
        self.sleep = sleep
        self.output = output or _terminal_output
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.processes: dict[str, Any] = {}
        self.readers: list[threading.Thread] = []
        self.logs: dict[str, _ProcessLog] = {}
        self._output_lock = threading.Lock()

    def run(self) -> int:
        """Run until interrupted or either child exits unexpectedly."""
        exit_code = 0
        try:
            web = self._spawn(
                "web",
                ["serve", "--log-level", self.log_level],
            )
            if not self._wait_for_web(web):
                return 1
            self._spawn(
                "worker",
                ["worker", "--log-level", self.log_level],
            )
            while True:
                for name, process in tuple(self.processes.items()):
                    code = process.poll()
                    if code is not None:
                        self._emit(
                            "start",
                            f"{name} exited unexpectedly with code {code}.",
                        )
                        return code if code != 0 else 1
                self.sleep(PROCESS_POLL_SECONDS)
        except KeyboardInterrupt:
            self._emit("start", "Stopping web and worker.")
        except (OSError, RuntimeError) as exc:
            self._emit("start", f"Unable to run local services: {type(exc).__name__}.")
            exit_code = 1
        finally:
            self._stop_children()
            self._close_logs()
        return exit_code

    def _spawn(self, name: str, arguments: list[str]):
        command = [sys.executable, "-m", "cli.main", *arguments]
        process = self.process_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.processes[name] = process
        if self.log_dir is not None:
            self.logs[name] = _ProcessLog(self.log_dir / f"{name}.log")
        if process.stdout is not None:
            reader = threading.Thread(
                target=self._read_output,
                args=(name, process.stdout),
                name=f"tradingagents-{name}-log",
                daemon=True,
            )
            reader.start()
            self.readers.append(reader)
        return process

    def _wait_for_web(self, web) -> bool:
        deadline = self.monotonic_clock() + self.startup_timeout
        while self.monotonic_clock() < deadline:
            code = web.poll()
            if code is not None:
                self._emit(
                    "start",
                    f"web exited before becoming healthy with code {code}.",
                )
                return False
            if self.health_probe(self.settings.port):
                self._emit(
                    "start",
                    f"Web is ready at http://127.0.0.1:{self.settings.port}.",
                )
                return True
            self.sleep(PROCESS_POLL_SECONDS)
        self._emit("start", "Web health check timed out.")
        return False

    def _read_output(self, name: str, stream: TextIO) -> None:
        for line in iter(stream.readline, ""):
            self._emit(name, line.rstrip("\n"))
        stream.close()

    def _emit(self, name: str, line: str) -> None:
        rendered = f"[{name}] {line}"
        with self._output_lock:
            self.output(rendered)
            process_log = self.logs.get(name)
            if process_log is not None:
                process_log.write(line)

    def _stop_children(self) -> None:
        active = [
            process
            for process in self.processes.values()
            if process.poll() is None
        ]
        for process in active:
            process.terminate()
        deadline = self.monotonic_clock() + self.shutdown_timeout
        while active and self.monotonic_clock() < deadline:
            active = [process for process in active if process.poll() is None]
            if active:
                self.sleep(PROCESS_POLL_SECONDS)
        for process in active:
            process.kill()
        for reader in self.readers:
            reader.join(timeout=1)

    def _close_logs(self) -> None:
        for process_log in self.logs.values():
            process_log.close()


def _health_probe(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/health",
            timeout=1,
        ) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _terminal_output(line: str) -> None:
    print(line, flush=True)
