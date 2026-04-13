import asyncio
import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class BatchManager:
    """
    BATCH 처리 LIMIT

    1. 한 배치당 최대 : 50_000
    2. 업로드 JSONL 파일 최대 ~100MB
    """
    URL_MAP = {
        "responses": "/v1/responses",
        "chat": "/v1/chat/completions",
        "embedding": "/v1/embeddings",
        "images": "/v1/images/generations",
    }

    def __init__(self, api_key: str | None = None):
        self._client = AsyncOpenAI(api_key=api_key)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.close()

    def _create_jsonl(
        self,
        messages: list[list[dict]],
        chat_bot_id: str,
        model: str,
        type_: str = "responses",
        text_format: dict | None = None,
    ) -> str:
        """JSONL 배치 파일을 생성합니다.

        type_ 별 messages 형식:

        responses / chat:
            각 항목은 role/content 딕셔너리의 리스트 (대화 메시지 목록)
            messages = [
                [{"role": "system", "content": "당신은 친절한 AI입니다."},
                 {"role": "user",   "content": "안녕하세요!"}],
                [{"role": "user", "content": "오늘 날씨는?"}],
            ]

        embedding:
            각 항목은 OpenAI Embeddings API 파라미터를 담은 딕셔너리 하나를 원소로 갖는 리스트.
            필수: input (str)
            선택: dimensions, encoding_format, user
            messages = [
                [{"input": "임베딩할 텍스트 1"}],
                [{"input": "임베딩할 텍스트 2", "dimensions": 512, "encoding_format": "float"}],
            ]

        images:
            각 항목은 OpenAI Images API 파라미터를 담은 딕셔너리 하나를 원소로 갖는 리스트.
            필수: prompt (str)
            선택: n, size, quality, response_format, background, output_format, output_compression, moderation
            messages = [
                [{"prompt": "A beautiful sunset over the ocean"}],
                [{"prompt": "A futuristic city at night", "size": "1024x1024", "quality": "hd", "n": 1}],
            ]
        """
        if type_ not in self.URL_MAP:
            raise ValueError(f"Invalid type: {type_!r}. Valid types: {list(self.URL_MAP)}")

        # type 검사는 여기서 한 번만 수행하고, 루프 안에서는 분기 없이 처리
        # embedding/images는 msg[0]의 파라미터를 그대로 언팩하여 API에 투명하게 전달
        build_body = {
            "responses": lambda msg: {
                "model": model,
                "input": msg,
                **({"text": text_format} if text_format else {}),
            },
            "chat":      lambda msg: {"model": model, "messages": msg},
            "embedding": lambda msg: {"model": model, **msg[0]},
            "images":    lambda msg: {"model": model, **msg[0]},
        }[type_]

        url = self.URL_MAP[type_]
        data = [
            {
                "custom_id": f"{chat_bot_id}-{i}",
                "method": "POST",
                "url": url,
                "body": build_body(message),
            }
            for i, message in enumerate(messages)
        ]

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        file_path = f"data/{chat_bot_id}-batch-{timestamp}-{secrets.token_hex(4)}.jsonl"
        jsonl_content = "\n".join(json.dumps(item, ensure_ascii=False) for item in data)

        os.makedirs("data", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(jsonl_content)

        return file_path

    def _parse_output_file(self, text: str) -> list[dict]:
        stripped = text.strip()
        if not stripped:
            return []
        return [json.loads(line) for line in stripped.split("\n")]

    async def _send_batch(self, file_path: str, type_: str = "responses", completion_window: str = "24h"):
        endpoint = self.URL_MAP[type_]

        # Read file bytes off the event loop to avoid blocking
        file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
        batch_input_file = await self._client.files.create(
            file=(Path(file_path).name, file_bytes),
            purpose="batch",
        )

        return await self._client.batches.create(
            input_file_id=batch_input_file.id,
            endpoint=endpoint,
            completion_window=completion_window,
        )

    async def request_batch(
        self,
        messages: list[list[dict]],
        chat_bot_id: str,
        model: str,
        type_: str = "responses",
        completion_window: str = "24h",
        text_format: dict | None = None,
    ):
        file_path = self._create_jsonl(messages, chat_bot_id, model, type_, text_format)
        batch_result = await self._send_batch(file_path, type_, completion_window)

        # Delete local JSONL after successful upload to avoid disk accumulation
        try:
            os.remove(file_path)
        except OSError as exc:
            logger.warning("Could not delete temp file %s: %s", file_path, exc)

        if batch_result.status == "failed":
            logger.error("Batch submission failed: %s", batch_result.errors)
            raise ValueError(f"Batch failed: {batch_result.errors}")

        return {
            "input_file_id": batch_result.input_file_id,
            "batch_id": batch_result.id,
            "status": batch_result.status,
        }

    async def check_status(self, batch_id: str):
        """
        HOW TO CHECK THE STATUS
        res.status : str

        STATUS
        1. validating : the input file is being validated before the batch can begin
        2. failed : the input file has failed the validation process
        3. in_progress : the input file was successfully validated and the batch is currently being run
        4. finalizing : the batch has completed and the results are being prepared
        5. completed : the batch has been completed and the results are ready
        6. expired : the batch was not able to be completed within the 24-hour time window
        7. cancelling : the batch is being cancelled (may take up to 10 minutes)
        8. cancelled : the batch was cancelled
        """
        return await self._client.batches.retrieve(batch_id)

    async def retrieve_output_file(self, output_file_id: str) -> list[dict]:
        """Fetch and parse a completed batch output file by its file ID."""
        result = await self._client.files.content(output_file_id)
        return self._parse_output_file(result.text)

    async def cancel_batch(self, batch_id: str):
        """
        STATUS
        cancelling -> cancelled
        """
        return await self._client.batches.cancel(batch_id)

    async def list_batches(self, limit: int = 10):
        """
        현재 배치 진행중인 목록 조회
        """
        return await self._client.batches.list(limit=limit)

    async def send_many(
        self,
        messages_list: list[list[list[dict]]],
        chat_bot_id: str,
        model: str,
        type_: str = "responses",
        text_format: dict | None = None,
    ) -> list:
        file_paths = []
        for messages in messages_list:
            file_path = self._create_jsonl(messages, chat_bot_id, model, type_, text_format)
            file_paths.append(file_path)

        try:
            return list(await asyncio.gather(
                *[self._send_batch(fp, type_) for fp in file_paths]
            ))
        finally:
            for fp in file_paths:
                try:
                    os.remove(fp)
                except OSError as exc:
                    logger.warning("Could not delete temp file %s: %s", fp, exc)
