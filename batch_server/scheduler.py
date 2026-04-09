import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

# Exponential backoff: 5 → 10 → 20 → 40 → 60min (capped)
_BACKOFF_MINUTES = [5, 10, 20, 40, 60]

# Module-level references so the job function (which must be pickle-safe)
# can reach the worker and scheduler without capturing instance state.
_worker = None
_scheduler: AsyncIOScheduler | None = None


def _next_delay(attempt: int) -> timedelta:
    minutes = _BACKOFF_MINUTES[min(attempt, len(_BACKOFF_MINUTES) - 1)]
    return timedelta(minutes=minutes)


def _schedule_next_check(batch_id: str, attempt: int) -> None:
    delay = _next_delay(attempt)
    run_at = datetime.now(timezone.utc) + delay
    _scheduler.add_job(
        _check_job,
        trigger="date",
        run_date=run_at,
        args=[batch_id, attempt],
        id=f"check_{batch_id}",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Scheduled check for batch %s at %s (attempt %d, delay %dmin)",
        batch_id, run_at, attempt, delay.seconds // 60,
    )


async def _check_job(batch_id: str, attempt: int) -> None:
    """Top-level async function — picklable by reference for RedisJobStore."""
    logger.info("Checking batch %s (attempt %d)", batch_id, attempt)
    status = await _worker.check_and_dispatch(batch_id)
    logger.info("Batch %s status: %s", batch_id, status)
    if status == "pending":
        _schedule_next_check(batch_id, attempt + 1)


class BatchScheduler:
    def __init__(self, worker, redis_url: str):
        global _worker, _scheduler

        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        db = int((parsed.path or "/0").lstrip("/") or 0)

        jobstores = {
            "default": RedisJobStore(host=host, port=port, db=db)
        }
        _scheduler = AsyncIOScheduler(jobstores=jobstores)
        _worker = worker
        self._scheduler = _scheduler

    def start(self) -> None:
        try:
            self._scheduler.start()
            logger.info("BatchScheduler started")
        except Exception as e:
            logger.error("Failed to start scheduler: %s", e)
            raise

    def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
            logger.info("BatchScheduler stopped")
        except Exception as e:
            logger.error("Error during scheduler shutdown: %s", e)

    def schedule_next_check(self, batch_id: str, attempt: int = 0) -> None:
        _schedule_next_check(batch_id, attempt)
