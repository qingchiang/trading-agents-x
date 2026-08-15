"""Periodic maintenance for runs moved to the recoverable trash."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.sqlite import SqliteSaver

from .repository import RunRepository
from .settings import AppSettings

TRASH_MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60
TRASH_MAINTENANCE_RETRY_SECONDS = 60 * 60
TRASH_PURGE_BATCH_SIZE = 50


class TrashMaintenance:
    """Purge expired trashed runs without introducing a scheduler dependency."""

    def __init__(
        self,
        settings: AppSettings,
        repository: RunRepository,
        *,
        utc_clock: Callable[[], datetime] | None = None,
        batch_size: int = TRASH_PURGE_BATCH_SIZE,
    ):
        self.settings = settings
        self.repository = repository
        self.utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self.batch_size = batch_size

    def run_once(self) -> int:
        """Purge all trashed runs expired at this maintenance cycle's cutoff."""
        retention_days = self.settings.trash_retention_days
        if retention_days == 0:
            return 0
        now = self.utc_clock()
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        cutoff = now - timedelta(days=retention_days)
        self._ensure_checkpoint_schema()
        purged = 0
        while True:
            batch = self.repository.purge_expired_trash(
                cutoff=cutoff,
                batch_size=self.batch_size,
            )
            purged += batch
            if batch == 0:
                return purged

    def _ensure_checkpoint_schema(self) -> None:
        with SqliteSaver.from_conn_string(
            str(self.settings.database_path)
        ) as saver:
            saver.conn.execute(
                f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}"
            )
            saver.setup()
