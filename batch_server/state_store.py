import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from redis.asyncio import Redis


@dataclass
class BatchState:
    batch_id: str
    service_name: str
    chat_bot_id: str
    status: str
    submitted_at: datetime
    expected_check_at: datetime
    file_path: str
    metadata: Optional[dict] = None
    errors: Optional[list] = None

    def to_redis_dict(self) -> dict:
        data = {
            "service_name": self.service_name,
            "chat_bot_id": self.chat_bot_id,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat(),
            "expected_check_at": self.expected_check_at.isoformat(),
            "file_path": self.file_path,
        }
        if self.metadata is not None:
            data["metadata"] = json.dumps(self.metadata)
        if self.errors is not None:
            data["errors"] = json.dumps(self.errors)
        return data

    @classmethod
    def from_redis_dict(cls, batch_id: str, data: dict) -> "BatchState":
        def decode(v):
            return v.decode() if isinstance(v, bytes) else v

        # Normalize all keys to strings (fakeredis returns byte keys)
        normalized = {decode(k): decode(v) for k, v in data.items()}

        # Check for required fields
        required_fields = ["service_name", "chat_bot_id", "status", "submitted_at", "expected_check_at", "file_path"]
        missing = [f for f in required_fields if f not in normalized]
        if missing:
            raise ValueError(f"Corrupted batch state for {batch_id}: missing fields {missing}")

        metadata = None
        if "metadata" in normalized:
            try:
                metadata = json.loads(normalized["metadata"])
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in metadata for batch {batch_id}: {e}")
        errors = None
        if "errors" in normalized:
            try:
                errors = json.loads(normalized["errors"])
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in errors for batch {batch_id}: {e}")

        return cls(
            batch_id=batch_id,
            service_name=normalized["service_name"],
            chat_bot_id=normalized["chat_bot_id"],
            status=normalized["status"],
            submitted_at=datetime.fromisoformat(normalized["submitted_at"]),
            expected_check_at=datetime.fromisoformat(normalized["expected_check_at"]),
            file_path=normalized["file_path"],
            metadata=metadata,
            errors=errors,
        )


class BatchStateStore:
    TTL_SECONDS = 50 * 3600  # 50h

    def __init__(self, redis: Redis):
        self._redis = redis

    def _key(self, batch_id: str) -> str:
        return f"batch:{batch_id}"

    async def save(self, state: BatchState) -> None:
        key = self._key(state.batch_id)
        await self._redis.hset(key, mapping=state.to_redis_dict())
        await self._redis.expire(key, self.TTL_SECONDS)

    async def get(self, batch_id: str) -> Optional[BatchState]:
        key = self._key(batch_id)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        return BatchState.from_redis_dict(batch_id, data)

    async def update_status(
        self, batch_id: str, status: str, errors: Optional[list] = None
    ) -> None:
        key = self._key(batch_id)
        updates: dict = {"status": status}
        if errors is not None:
            updates["errors"] = json.dumps(errors)
        await self._redis.hset(key, mapping=updates)

    async def delete(self, batch_id: str) -> None:
        await self._redis.delete(self._key(batch_id))

    async def list_by_status(self, status: str) -> list[BatchState]:
        results = []
        async for key in self._redis.scan_iter("batch:*"):
            raw_key = key.decode() if isinstance(key, bytes) else key
            batch_id = raw_key.split(":", 1)[1]
            data = await self._redis.hgetall(key)
            # hgetall may return byte keys; check both
            raw_status = data.get(b"status", data.get("status", b""))
            if isinstance(raw_status, bytes):
                raw_status = raw_status.decode()
            if raw_status == status:
                results.append(BatchState.from_redis_dict(batch_id, data))
        return results
