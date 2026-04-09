import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from batch_manager import BatchManager
from service_registry import ServiceRegistry
from state_store import BatchState, BatchStateStore

logger = logging.getLogger(__name__)

# First status check 5 minutes after submission (exponential backoff handled by scheduler)
_FIRST_CHECK_DELAY = timedelta(minutes=5)


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
    ) -> BatchState:
        result = await self._manager.request_batch(
            messages=messages,
            chat_bot_id=chat_bot_id,
            model=model,
            type=type_,
            completion_window=completion_window,
        )
        now = datetime.now(timezone.utc)
        state = BatchState(
            batch_id=result["batch_id"],
            service_name=service_name,
            chat_bot_id=chat_bot_id,
            status=result["status"],
            submitted_at=now,
            expected_check_at=now + _FIRST_CHECK_DELAY,
            file_path=result["file_path"],
            metadata=metadata,
        )
        await self._store.save(state)
        return state

    def _parse_results(self, items: list[dict]) -> list[dict]:
        results = []
        for item in items:
            text_content = ""
            response = item.get("response") or {}
            body = response.get("body") or {}
            if "output" in body:
                for out in body["output"]:
                    for content in out.get("content", []):
                        if content.get("type") == "output_text":
                            text_content = content["text"]
            elif "choices" in body:
                if body["choices"] and "message" in body["choices"][0]:
                    text_content = body["choices"][0]["message"].get("content", "")
            results.append({
                "custom_id": item["custom_id"],
                "text": text_content,
                "error": item.get("error"),
            })
        return results

    async def check_and_dispatch(self, batch_id: str) -> str:
        """Returns: 'completed' | 'failed' | 'expired' | 'pending' | 'not_found'"""
        state = await self._store.get(batch_id)
        if not state:
            return "not_found"

        batch_result = await self._manager.check_status(batch_id)
        status = batch_result.status

        if status == "completed":
            output = await self._manager.retrieve_output_file(batch_id)
            results = self._parse_results(output)
            success = await self._send_callback(state, "completed", results)
            if success:
                await self._store.delete(batch_id)
            return "completed"

        if status in ("failed", "expired"):
            errors = []
            if batch_result.errors:
                errors = [
                    {"code": e.code, "message": e.message}
                    for e in batch_result.errors.data
                ]
            success = await self._send_callback(state, status, [], errors)
            # Note: callback failure is handled inside _send_callback() which
            # marks status as "callback_failed". The success check here prevents
            # overwriting that "callback_failed" status with the original "failed"/"expired".
            if success:
                await self._store.update_status(batch_id, status, errors or None)
            return status

        return "pending"

    async def _send_callback(
        self,
        state: BatchState,
        status: str,
        results: list,
        errors: Optional[list] = None,
    ) -> bool:
        service = self._registry.get(state.service_name)
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

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
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
