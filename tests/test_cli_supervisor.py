from __future__ import annotations

from io import StringIO
from pathlib import Path

from cli.supervisor import LocalProcessSupervisor, _ProcessLog


class _FakeProcess:
    def __init__(self, poll_results=None):
        self.stdout = None
        self.poll_results = list(poll_results or ())
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.poll_results:
            result = self.poll_results.pop(0)
            if result is not None:
                self.returncode = result
            return result
        return None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_supervisor_health_gates_worker_and_stops_sibling(
    app_settings,
) -> None:
    web = _FakeProcess()
    worker = _FakeProcess([2])
    processes = iter((web, worker))
    commands = []
    output = []

    def factory(command, **_kwargs):
        commands.append(command)
        return next(processes)

    supervisor = LocalProcessSupervisor(
        app_settings,
        process_factory=factory,
        health_probe=lambda _port: True,
        sleep=lambda _seconds: None,
        output=output.append,
    )

    assert supervisor.run() == 2
    assert commands[0][-3:] == ["serve", "--log-level", "info"]
    assert commands[1][-3:] == ["worker", "--log-level", "info"]
    assert web.terminated is True
    assert any(line.startswith("[start] Web is ready") for line in output)
    assert any("worker exited unexpectedly with code 2" in line for line in output)


def test_supervisor_interrupt_stops_both_children(app_settings) -> None:
    web = _FakeProcess()
    worker = _FakeProcess()
    processes = iter((web, worker))

    def factory(_command, **_kwargs):
        return next(processes)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    supervisor = LocalProcessSupervisor(
        app_settings,
        process_factory=factory,
        health_probe=lambda _port: True,
        sleep=interrupt,
        output=lambda _line: None,
    )

    assert supervisor.run() == 0
    assert web.terminated is True
    assert worker.terminated is True


def test_supervisor_prefixes_each_child_output(app_settings) -> None:
    output = []
    supervisor = LocalProcessSupervisor(app_settings, output=output.append)

    supervisor._read_output("web", StringIO("first\nsecond\n"))

    assert output == ["[web] first", "[web] second"]


def test_optional_process_log_rotates(tmp_path: Path) -> None:
    path = tmp_path / "worker.log"
    process_log = _ProcessLog(path, max_bytes=40, backup_count=2)
    try:
        for index in range(10):
            process_log.write(f"worker line {index}")
    finally:
        process_log.close()

    assert path.is_file()
    assert (tmp_path / "worker.log.1").is_file()
