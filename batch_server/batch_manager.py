import asyncio
import json
import logging
import os
from datetime import datetime

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class BatchManager:
    """
    BATCH 처리 LIMIT

    1. 한 배칭당 최대 : 50_000
    2. 업로드 JSONL 파일 최대 ~100MB
    """
    URL_MAP = {
        "responses": "/v1/responses",
        "chat": "/v1/chat/completions",
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
    ) -> str:
        """
        messages = [[{"role": "system", "content": "..."}, {"role": "user", "content": "hello"}],[{"role": "user", "content": "hello"}]]
        """
        data = []
        for i, message in enumerate(messages):
            custom_id = f"{chat_bot_id}-{i}"

            if type_ == "responses":
                data.append({
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": self.URL_MAP[type_],
                    "body": {
                        "model": model,
                        "input": message,
                    },
                })
            elif type_ == "chat":
                data.append({
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": self.URL_MAP[type_],
                    "body": {
                        "model": model,
                        "messages": message,
                    },
                })
            else:
                raise ValueError(f"Invalid type: {type_}")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        file_path = f"data/{chat_bot_id}-batch-{timestamp}.jsonl"
        jsonl_content = "\n".join(json.dumps(item, ensure_ascii=False) for item in data)

        os.makedirs("data", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(jsonl_content)

        return file_path

    def _parse_output_file(self, text: str) -> list[dict]:
        lines = text.strip().split("\n")
        return [json.loads(line) for line in lines]

    async def _send_batch(self, file_path: str, type_: str = "responses", completion_window: str = "24h"):
        endpoint = self.URL_MAP[type_]

        with open(file_path, "rb") as f:
            batch_input_file = await self._client.files.create(
                file=f,
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
    ):
        file_path = self._create_jsonl(messages, chat_bot_id, model, type_)
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
            "file_path": file_path,
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
    ) -> list:
        file_paths = []
        for messages in messages_list:
            file_path = self._create_jsonl(messages, chat_bot_id, model, type_)
            file_paths.append(file_path)

        return list(await asyncio.gather(
            *[self._send_batch(fp, type_) for fp in file_paths]
        ))
