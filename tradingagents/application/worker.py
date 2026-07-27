"""Single-concurrency database worker for local deployments."""

from __future__ import annotations

import logging
import signal
import threading
from uuid import uuid4

from .outcomes import OutcomeSettlement
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
        worker_id: str | None = None,
    ):
        self.settings = settings
        self.service = service or AnalysisService(settings)
        self.repository = self.service.repository
        self.settlement = settlement or OutcomeSettlement(
            settings,
            self.repository,
        )
        self.worker_id = worker_id or f"worker:{uuid4()}"
        self.stop_event = threading.Event()

    def run_once(self) -> bool:
        claimed = self.repository.claim_next(
            self.worker_id,
            self.settings.lease_seconds,
        )
        if claimed is None:
            self.settlement.settle_once(limit=10)
            return False
        try:
            self.service.execute_claimed(
                claimed,
                worker_id=self.worker_id,
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

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def request_stop(_signum, _frame) -> None:
            self.stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
