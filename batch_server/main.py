import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from batch_manager import BatchManager
from scheduler import BatchScheduler
from service_registry import ServiceRegistry
from state_store import BatchStateStore
from worker import BatchWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    redis_url: str  # required — set REDIS_URL in .env or environment


settings = Settings()

redis_client = Redis.from_url(settings.redis_url)
store = BatchStateStore(redis_client)
registry = ServiceRegistry("config.yaml")
_batch_manager = BatchManager(api_key=settings.openai_api_key or None)
worker = BatchWorker(_batch_manager, store, registry)
scheduler = BatchScheduler(worker, settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.openai_api_key:
        logger.warning(
            "OPENAI_API_KEY is not set — all OpenAI API calls will fail at runtime"
        )
    scheduler.start()
    yield
    scheduler.shutdown()
    await redis_client.aclose()


app = FastAPI(title="Batch Automation Service", lifespan=lifespan)


class BatchRequest(BaseModel):
    messages: list[list[dict]]
    service_name: str
    chat_bot_id: str
    model: Optional[str] = None
    type: Optional[str] = None
    completion_window: str = "24h"
    metadata: Optional[dict] = None


@app.post("/batches")
async def submit_batch(req: BatchRequest):
    if not registry.exists(req.service_name):
        raise HTTPException(status_code=422, detail=f"Unknown service: {req.service_name}")

    svc = registry.get(req.service_name)
    model = req.model or svc.default_model
    type_ = req.type or svc.default_type

    state = await worker.submit(
        messages=req.messages,
        service_name=req.service_name,
        chat_bot_id=req.chat_bot_id,
        model=model,
        type_=type_,
        completion_window=req.completion_window,
        metadata=req.metadata,
    )
    scheduler.schedule_next_check(state.batch_id, attempt=0)

    return {
        "batch_id": state.batch_id,
        "status": state.status,
        "expected_check_at": state.expected_check_at.isoformat(),
    }


@app.get("/batches/{batch_id}")
async def get_batch(batch_id: str, refresh: bool = False):
    state = await store.get(batch_id)
    if not state:
        raise HTTPException(status_code=404, detail="Batch not found")
    if refresh:
        await worker.check_and_dispatch(batch_id)
        state = await store.get(batch_id)
        if not state:
            return {"batch_id": batch_id, "status": "completed", "detail": "Batch completed and removed"}
    return {
        "batch_id": state.batch_id,
        "service_name": state.service_name,
        "chat_bot_id": state.chat_bot_id,
        "status": state.status,
        "submitted_at": state.submitted_at.isoformat(),
        "expected_check_at": state.expected_check_at.isoformat(),
        "input_file_id": state.input_file_id,
        "metadata": state.metadata,
        "errors": state.errors,
    }


@app.get("/batches")
async def list_batches(status: Optional[str] = None):
    if not status:
        raise HTTPException(status_code=400, detail="status query parameter required")
    states = await store.list_by_status(status)
    return [
        {
            "batch_id": s.batch_id,
            "service_name": s.service_name,
            "chat_bot_id": s.chat_bot_id,
            "status": s.status,
            "submitted_at": s.submitted_at.isoformat(),
            "errors": s.errors,
        }
        for s in states
    ]


@app.delete("/batches/{batch_id}")
async def delete_batch(batch_id: str):
    state = await store.get(batch_id)
    if not state:
        raise HTTPException(status_code=404, detail="Batch not found")

    # Stop the scheduled polling job so we don't check a deleted batch
    scheduler.remove_job(batch_id)

    # Cancel the batch on OpenAI if it's still in progress
    try:
        await _batch_manager.cancel_batch(batch_id)
    except Exception as exc:
        logger.warning("Could not cancel OpenAI batch %s: %s", batch_id, exc)

    await store.delete(batch_id)
    return {"deleted": batch_id}
