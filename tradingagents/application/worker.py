"""Single-concurrency database worker for local deployments."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from time import monotonic
from uuid import uuid4

from .maintenance import (
    TRASH_MAINTENANCE_INTERVAL_SECONDS,
    TRASH_MAINTENANCE_RETRY_SECONDS,
    TrashMaintenance,
)
from .outcomes import OutcomeSettlement
from .runtime import WorkerShutdown
from .service import AnalysisService
from .settings import AppSettings

logger = logging.getLogger(__name__)


class AnalysisWorker:
    def __init__(
        self,
        settings: AppSettings,
        *,
        service: AnalysisService | None = None,
        settlement: OutcomeSettlement | None = None,
        maintenance: TrashMaintenance | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        worker_id: str | None = None,
    ):
        self.settings = settings
        self.service = service or AnalysisService(settings)
        self.repository = self.service.repository
        # Retain the injectable attribute for transitional callers, but the
        # active worker no longer settles legacy outcomes while idle.
        self.settlement = settlement
        self.maintenance = maintenance or TrashMaintenance(
            settings,
            self.repository,
        )
        self.monotonic_clock = monotonic_clock
        self._next_maintenance_at = float("-inf")
        self.worker_id = worker_id or f"worker:{uuid4()}"
        self.stop_event = threading.Event()

    def run_once(self) -> bool:
        self._run_maintenance_if_due()
        claimed = self.repository.claim_next(
            self.worker_id,
            self.settings.lease_seconds,
        )
        if claimed is None:
            return False
        try:
            self.service.execute_claimed(
                claimed,
                worker_id=self.worker_id,
                shutdown_requested=self.stop_event.is_set,
            )
        except WorkerShutdown:
            logger.info(
                "analysis run %s returned to queue during worker shutdown",
                claimed.id,
            )
        except Exception:
            logger.exception("analysis run %s failed", claimed.id)
        return True

    def serve_forever(self) -> None:
        self._install_signal_handlers()
        while not self.stop_event.is_set():
            worked = self.run_once()
            if not worked:
                self.stop_event.wait(self.settings.worker_poll_seconds)

    def stop(self) -> None:
        self.stop_event.set()

    def _run_maintenance_if_due(self) -> None:
        now = self.monotonic_clock()
        if now < self._next_maintenance_at:
            return
        try:
            self.maintenance.run_once()
        except Exception as exc:
            self._next_maintenance_at = (
                self.monotonic_clock()
                + TRASH_MAINTENANCE_RETRY_SECONDS
            )
            logger.warning(
                "trash maintenance failed; retry scheduled: %s",
                type(exc).__name__,
            )
            return
        self._next_maintenance_at = (
            self.monotonic_clock()
            + TRASH_MAINTENANCE_INTERVAL_SECONDS
        )

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def request_stop(_signum, _frame) -> None:
            self.stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
