import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from batch_manager import BatchManager
from scheduler import FIRST_CHECK_DELAY
from service_registry import ServiceRegistry
from state_store import BatchState, BatchStateStore

logger = logging.getLogger(__name__)


class BatchWorker:
    def __init__(
        self,
        batch_manager: BatchManager,
        state_store: BatchStateStore,
        registry: ServiceRegistry,
    ):
        self._manager = batch_manager
        self._store = state_store
        self._registry = registry

    async def submit(
        self,
        messages: list[list[dict]],
        service_name: str,
        chat_bot_id: str,
        model: str,
        type_: str,
        completion_window: str = "24h",
        metadata: Optional[dict] = None,
        text_format: Optional[dict] = None,
    ) -> BatchState:
        result = await self._manager.request_batch(
            messages=messages,
            chat_bot_id=chat_bot_id,
            model=model,
            type_=type_,
            completion_window=completion_window,
            text_format=text_format,
        )
        now = datetime.now(timezone.utc)
        state = BatchState(
            batch_id=result["batch_id"],
            service_name=service_name,
            chat_bot_id=chat_bot_id,
            status=result["status"],
            submitted_at=now,
            expected_check_at=now + FIRST_CHECK_DELAY,
            input_file_id=result["input_file_id"],
            metadata=metadata,
        )
        await self._store.save(state)
        return state

    def _parse_results(self, items: list[dict]) -> list[dict]:
        results = []
        for item in items:
            content = ""
            response = item.get("response") or {}
            body = response.get("body") or {}
            if "output" in body:
                # responses API
                for out in body["output"]:
                    for c in out.get("content", []):
                        if c.get("type") == "output_text":
                            content = c["text"]
            elif "choices" in body:
                # chat completions API
                if body["choices"] and "message" in body["choices"][0]:
                    content = body["choices"][0]["message"].get("content", "")
            elif "data" in body:
                first = body["data"][0] if body["data"] else {}
                if "embedding" in first:
                    # embeddings API — embedding 벡터를 JSON 문자열로 직렬화
                    content = json.dumps(first["embedding"])
                elif "url" in first:
                    # images API — 생성된 이미지 URL
                    content = first["url"]
            results.append({
                "custom_id": item["custom_id"],
                "content": content,
                "error": item.get("error"),
            })
        return results

    async def check_and_dispatch(self, batch_id: str) -> str:
        """Returns: 'completed' | 'failed' | 'expired' | 'cancelled' | 'pending' | 'not_found'"""
        if not await self._store.acquire_lock(batch_id):
            logger.info("Batch %s check already in progress, skipping", batch_id)
            return "pending"

        try:
            state = await self._store.get(batch_id)
            if not state:
                return "not_found"

            batch_result = await self._manager.check_status(batch_id)
            status = batch_result.status

            if status == "completed":
                output_file_id = batch_result.output_file_id
                if not output_file_id:
                    logger.error("Batch %s completed but output_file_id is missing", batch_id)
                    await self._store.update_status(batch_id, "failed")
                    return "failed"
                output = await self._manager.retrieve_output_file(output_file_id)
                results = self._parse_results(output)
                success = await self._send_callback(state, "completed", results)
                if success:
                    await self._store.delete(batch_id)
                return "completed"

            if status in ("failed", "expired", "cancelled"):
                errors = []
                if batch_result.errors:
                    errors = [
                        {"code": e.code, "message": e.message}
                        for e in batch_result.errors.data
                    ]
                success = await self._send_callback(state, status, [], errors)
                # Note: callback failure is handled inside _send_callback() which
                # marks status as "callback_failed". The success check here prevents
                # overwriting that "callback_failed" status with the original status.
                if success:
                    await self._store.update_status(batch_id, status, errors or None)
                return status

            return "pending"
        finally:
            await self._store.release_lock(batch_id)

    async def _send_callback(
        self,
        state: BatchState,
        status: str,
        results: list,
        errors: Optional[list] = None,
    ) -> bool:
        service = self._registry.get(state.service_name)
        if service is None:
            logger.error(
                "No service config for '%s', cannot send callback for batch %s",
                state.service_name, state.batch_id,
            )
            await self._store.update_status(state.batch_id, "callback_failed")
            return False

        payload: dict = {
            "batch_id": state.batch_id,
            "service_name": state.service_name,
            "chat_bot_id": state.chat_bot_id,
            "status": status,
            "metadata": state.metadata,
            "results": results,
        }
        if errors:
            payload["errors"] = errors

        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(service.callback_url, json=payload)
                    resp.raise_for_status()
                    return True
                except Exception as exc:
                    if attempt == 2:
                        logger.error(
                            "Callback failed after 3 attempts for batch %s: %s",
                            state.batch_id, exc,
                        )
                        await self._store.update_status(state.batch_id, "callback_failed")
                        return False
                    await asyncio.sleep(2 ** attempt)
