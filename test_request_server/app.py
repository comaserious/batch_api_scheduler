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
                        {"role": "user", "content": """  이름 :   홍길동  
나이:29
직업  :개발자   

기술스택:python,  fastapi ,docker,postgresql  

경력:
- 2021~2022  스타트업   백엔드 개발
-2022~현재:  중견기업   서버 개발

  이메일 :  test@example.com  
  다음 정보를 이용해서 구조화된 데이터를 생성해줘
  """}

                    ],
                ],
                "service_name": "test_service",
                "chat_bot_id": "test_chat_bot_01",
                "model": "gpt-4.1",
                "type": "responses",
                "metadata": {
                    "db_name": "naraone",
                    "description": "제대로 동작하는지 확인해보자"
                },
                "text_format": {
                    "format": {
                        "type": "json_schema",
                        "name": "profile_output",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "age": {"type": "integer"},
                                "job": {"type": "string"},
                                "tech_stack": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "career": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "period": {"type": "string"},
                                            "description": {"type": "string"}
                                        },
                                        "required": ["period", "description"],
                                        "additionalProperties": False
                                    }
                                },
                                "email": {"type": "string"}
                            },
                            "required": ["name", "age", "job", "tech_stack", "career", "email"],
                            "additionalProperties": False
                        }
                    }
                }
            }

            response = await client.post("http://localhost:15000/batches", json=sample_data)
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
    
    for key, value in request.model_dump().items():
        print("--------------------------------")
        print(f"KEY : {key}")
        print(f"VALUE : {value}")
        print("--------------------------------")

    ####################################
    # DB 저장 로직 
    ####################################
    return {"status": "success", "data": request.model_dump()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=1818)

