from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

import httpx

@app.get("/sample/batch")
async def test_batch():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            sample_data = {
                "messages": [
                    [
                        {"role": "system", "content": "당신은 친절한 AI 봇입니다. 사용자의 질문에 한글로 답변을 하세요"},
                        {"role": "user", "content": "간단한 로또 추천 파이선 코드를 작성해줘"}
                    ],
                    [
                        {"role": "user", "content": "한국의 제철음식을 마크다운 표로 작성을 해줘"}
                    ]
                ],
                "service_name": "test_service",
                "chat_bot_id": "test_chat_bot_01",
                "model": "gpt-4.1",
                "type": "responses",
                "metadata": {
                    "db_name": "naraone",
                    "description": "제대로 동작하는지 확인해보자"
                }
            }

            response = await client.post("http://localhost:8000/batches", json=sample_data)
            response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except httpx.RequestError as e:
        return {"error": str(e)}
    except httpx.HTTPStatusError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

from pydantic import BaseModel

class BatchRequest(BaseModel):
    batch_id: str
    service_name: str
    chat_bot_id: str
    status: str
    metadata: dict
    results: list

@app.post("/test/response")
async def test_response(request: BatchRequest):
    print(request.model_dump())
    for key, value in request.model_dump().items():
        print(key, value)
    return {"status": "success", "data": request.model_dump()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=1818)

