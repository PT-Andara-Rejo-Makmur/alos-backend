"""Run the existing job worker against PostgreSQL and the shared document object store."""

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from alos.config import get_settings
from alos.contracts import CanonicalContractCatalog
from alos.documents.ingestion import DocumentIngestionService
from alos.documents.object_store import DocumentObjectStore
from alos.domains.record_repository import RecordRepository
from alos.domains.shared_work import SharedWorkService
from alos.jobs.models import JobType
from alos.jobs.repository import JobConflictError
from alos.jobs.sql_repository import SqlJobRepository
from alos.jobs.worker import JobWorker
from alos.notifications.business import BusinessNotifications
from alos.persistence.database import Database


async def run() -> None:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = []
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, stopping.set)
            installed_signals.append(stop_signal)
        except NotImplementedError:
            # Windows handles interactive interruption through asyncio.run.
            pass
    settings = get_settings()
    database = Database(settings.DATABASE_URL)
    if settings.ALOS_CONTRACTS_PATH is None:
        raise ValueError("ALOS_CONTRACTS_PATH is required for the governed worker")
    contracts = CanonicalContractCatalog(settings.ALOS_CONTRACTS_PATH)
    records = RecordRepository(database.session_factory, contracts)
    work = SharedWorkService(database.session_factory)
    documents = DocumentIngestionService(
        records,
        work,
        DocumentObjectStore(settings.DOCUMENT_OBJECT_ROOT),
        settings.DOCUMENT_UPLOAD_MAX_BYTES,
    )
    jobs = SqlJobRepository(database.session_factory)
    notifications = BusinessNotifications(records)
    worker = JobWorker(
        jobs,
        {
            JobType.DOCUMENT_EXTRACTION.value: documents.extract_job,
            JobType.NOTIFICATION.value: notifications.deliver,
        },
        worker_id="business." + uuid4().hex,
    )
    recovered_at = 0.0
    try:
        while not stopping.is_set():
            if monotonic() - recovered_at > 60:
                await jobs.recover_stale(stale_before=datetime.now(UTC) - timedelta(minutes=5))
                await notifications.schedule_due()
                recovered_at = monotonic()
            try:
                result = await worker.run_once()
            except JobConflictError:
                logging.getLogger(__name__).info("Worker lease superseded; result discarded")
                result = None
            if result is None:
                try:
                    async with asyncio.timeout(1):
                        await stopping.wait()
                except TimeoutError:
                    pass
    finally:
        await database.dispose()
        for stop_signal in installed_signals:
            loop.remove_signal_handler(stop_signal)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
