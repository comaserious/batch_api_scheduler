# OpenAI Batch Scheduler

OpenAI [Batch API](https://platform.openai.com/docs/guides/batch)를 자동화하는 FastAPI 서버입니다.

배치 요청을 제출하면 지수 백오프 방식으로 완료 여부를 자동 폴링하고, 완료 시 등록된 콜백 URL로 결과를 전송합니다.

## 아키텍처

```
클라이언트
   │
   ▼
FastAPI (main.py)
   ├── POST /batches        → BatchWorker.submit()
   ├── GET  /batches/{id}   → BatchStateStore.get()
   ├── GET  /batches        → BatchStateStore.list_by_status()
   └── DELETE /batches/{id} → OpenAI cancel + Redis 삭제

BatchWorker
   ├── BatchManager         → OpenAI Batch API 호출
   ├── BatchStateStore      → Redis 상태 저장
   └── ServiceRegistry      → 서비스별 콜백 URL / 모델 설정

BatchScheduler (APScheduler + RedisJobStore)
   └── 지수 백오프 폴링 (5 → 10 → 20 → 40 → 60분)
         └── 완료 시 → 콜백 URL로 POST
```

## 지원 API 타입

| type | OpenAI API | messages 형식 |
|------|-----------|--------------|
| `responses` | `/v1/responses` | role/content 대화 목록 |
| `chat` | `/v1/chat/completions` | role/content 대화 목록 |
| `embedding` | `/v1/embeddings` | `[{"input": "텍스트", ...}]` |
| `images` | `/v1/images/generations` | `[{"prompt": "프롬프트", ...}]` |

### messages 형식 상세

`messages`는 항상 `list[list[dict]]` 형태입니다. 바깥 리스트의 각 항목이 하나의 배치 요청이 됩니다.

#### `responses` / `chat`

각 요청은 role/content 딕셔너리의 리스트입니다.

```json
{
  "type": "responses",
  "messages": [
    [
      {"role": "system", "content": "당신은 친절한 AI입니다."},
      {"role": "user",   "content": "안녕하세요!"}
    ],
    [
      {"role": "user", "content": "오늘 날씨는?"}
    ]
  ]
}
```

#### `embedding`

각 요청은 [OpenAI Embeddings API](https://platform.openai.com/docs/api-reference/embeddings/create) 파라미터를 담은 딕셔너리 하나를 원소로 하는 리스트입니다.

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `input` | 필수 | 임베딩할 텍스트 |
| `dimensions` | 선택 | 출력 벡터 차원 수 (text-embedding-3 이상) |
| `encoding_format` | 선택 | `float` 또는 `base64` (기본값: `float`) |
| `user` | 선택 | 최종 사용자 식별 ID |

```json
{
  "type": "embedding",
  "messages": [
    [{"input": "임베딩할 텍스트 1"}],
    [{"input": "임베딩할 텍스트 2", "dimensions": 512, "encoding_format": "float"}]
  ]
}
```

결과 `content`는 임베딩 벡터를 JSON 직렬화한 문자열로 반환됩니다.

#### `images`

각 요청은 [OpenAI Images API](https://platform.openai.com/docs/api-reference/images/create) 파라미터를 담은 딕셔너리 하나를 원소로 하는 리스트입니다.

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `prompt` | 필수 | 이미지 생성 프롬프트 |
| `n` | 선택 | 생성할 이미지 수 (1~10) |
| `size` | 선택 | `1024x1024`, `1536x1024` 등 |
| `quality` | 선택 | `standard`, `hd`, `low`, `medium`, `high`, `auto` |
| `response_format` | 선택 | `url` 또는 `b64_json` |
| `background` | 선택 | `transparent`, `opaque`, `auto` |
| `output_format` | 선택 | `png`, `jpeg`, `webp` |
| `output_compression` | 선택 | 0~100 압축 수준 |
| `moderation` | 선택 | `low` 또는 `auto` |

```json
{
  "type": "images",
  "messages": [
    [{"prompt": "A beautiful sunset over the ocean"}],
    [{"prompt": "A futuristic city at night", "size": "1024x1024", "quality": "hd", "n": 1}]
  ]
}
```

결과 `content`는 생성된 이미지 URL로 반환됩니다.

## Structured Output (JSON Schema)

`type: "responses"` 사용 시 `text_format` 파라미터로 응답 형식을 JSON Schema로 강제할 수 있습니다.

### 지원 조건

- `type: "responses"` 전용 (`chat`, `embedding`, `images`에는 적용되지 않음)
- 모델: `gpt-4o-2024-08-06` 이상 또는 `gpt-4o-mini` 이상 필수

### 요청 예시

```json
{
  "messages": [
    [
      {"role": "system", "content": "You are a translator. Translate the given text."},
      {"role": "user", "content": "Translate: hello, world"}
    ]
  ],
  "service_name": "my_service",
  "chat_bot_id": "bot_001",
  "model": "gpt-4o-2024-08-06",
  "type": "responses",
  "text_format": {
    "format": {
      "type": "json_schema",
      "name": "translation_output",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "translations": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "index": {"type": "integer"},
                "text": {"type": "string"}
              },
              "required": ["index", "text"],
              "additionalProperties": false
            }
          }
        },
        "required": ["translations"],
        "additionalProperties": false
      }
    }
  }
}
```

콜백으로 수신되는 `results[].content`는 스키마에 맞는 JSON 문자열입니다.

```json
{
  "results": [
    {
      "custom_id": "bot_001-0",
      "content": "{\"translations\": [{\"index\": 0, \"text\": \"안녕, 세계\"}]}",
      "error": null
    }
  ]
}
```

### `strict: true` 사용 시 스키마 규칙

| 규칙 | 설명 |
|------|------|
| 모든 필드를 `required`에 명시 | 선택 필드도 포함해야 함 |
| `additionalProperties: false` | 모든 오브젝트에 필수 |
| 중첩 오브젝트에도 동일 규칙 적용 | 재귀적으로 적용됨 |

---

## 배포 전략

### 핵심 원칙

dev에서 검증한 **이미지를 그대로** prod에 올립니다. dev와 prod가 동일한 이미지 태그를 사용해야 "dev OK = prod OK"가 보장됩니다.

```
빌드 1회 → dev 검증 → 동일 이미지 그대로 prod 배포
```

### 배포 파일 구조

```
batch_server/
├── docker-compose.dev.yml   # dev (포트 15000, ROOT_PATH=/v1/dev/batch)
├── docker-compose.prod.yml  # prod (포트 5000,  ROOT_PATH=/v1/batch)
└── deploy.sh                # 빌드 + 배포 스크립트
```

### 1단계 — 사전 준비 (최초 1회)

```bash
# Redis와 공유할 Docker 네트워크 생성
docker network create batch-redis
```

`.env` 파일을 생성합니다.

```env
OPENAI_API_KEY=sk-...
REDIS_URL=redis://redis:6379
```

`config.yaml`에 서비스를 등록합니다.

```yaml
services:
  my_service:
    callback_url: http://my-service/api/batch-result
    default_model: gpt-4.1
    default_type: responses
```

### 2단계 — 빌드 + dev 배포

```bash
cd batch_server
./deploy.sh 1.0.0 dev
```

`batch-automation:1.0.0` 이미지를 빌드하고 dev(15000포트)에 올립니다.

### 3단계 — dev 검증

```
https://api-aipro.chatbaram.com/v1/dev/batch/docs
```

### 4단계 — 동일 이미지를 prod에 배포

검증이 완료되면 **재빌드 없이** 같은 이미지를 prod에 올립니다.

```bash
VERSION=1.0.0 docker compose -f docker-compose.prod.yml up -d --no-build
```

> `--no-build` 가 핵심입니다. 재빌드 없이 dev에서 검증한 이미지 그대로 prod에 올립니다.

### 롤백

```bash
# 이전 버전으로 즉시 롤백
VERSION=0.9.0 docker compose -f docker-compose.prod.yml up -d --no-build
```

### deploy.sh 사용법

```bash
./deploy.sh <VERSION> [dev|prod]

./deploy.sh 1.0.0          # 빌드만
./deploy.sh 1.0.0 dev      # 빌드 + dev 배포
./deploy.sh 1.0.0 prod     # 빌드 + prod 배포
```

### 방식 비교

| 항목 | `build: .` 방식 | 이미지 태깅 방식 |
|------|----------------|----------------|
| dev 검증 신뢰도 | 낮음 (dev ≠ prod) | 높음 (완전 동일) |
| 롤백 | 어려움 | `VERSION=이전버전` 한 줄 |
| 빌드 횟수 | 배포마다 재빌드 | 버전당 1회 |

## API

### 배치 제출

```http
POST /batches
```

```json
{
  "messages": [
    [
      {"role": "system", "content": "당신은 친절한 AI입니다."},
      {"role": "user", "content": "안녕하세요!"}
    ],
    [
      {"role": "user", "content": "오늘 날씨는?"}
    ]
  ],
  "service_name": "my_service",
  "chat_bot_id": "bot_001",
  "model": "gpt-4.1",
  "type": "responses",
  "completion_window": "24h",
  "metadata": {
    "db_name": "mydb",
    "description": "추가 정보"
  },
  "text_format": null
}
```

`text_format`은 선택 파라미터입니다. JSON Schema로 응답 형식을 강제하려면 [Structured Output](#structured-output-json-schema) 섹션을 참고하세요.

**응답**

```json
{
  "batch_id": "batch_abc123",
  "status": "validating",
  "expected_check_at": "2024-01-01T00:05:00+00:00"
}
```

---

### 배치 상태 조회

```http
GET /batches/{batch_id}
GET /batches/{batch_id}?refresh=true   # OpenAI에 즉시 재확인
```

---

### 배치 목록 조회

```http
GET /batches?status=pending
```

가능한 status 값: `validating`, `in_progress`, `finalizing`, `completed`, `failed`, `expired`, `cancelled`, `callback_failed`

---

### 배치 취소 및 삭제

```http
DELETE /batches/{batch_id}
```

OpenAI 배치를 취소하고 Redis에서 상태를 삭제합니다.

---

## 콜백

배치가 완료되면 `config.yaml`에 등록된 `callback_url`로 다음 형식의 `POST` 요청이 전송됩니다.

```json
{
  "batch_id": "batch_abc123",
  "service_name": "my_service",
  "chat_bot_id": "bot_001",
  "status": "completed",
  "metadata": { "db_name": "mydb" },
  "results": [
    {
      "custom_id": "bot_001-0",
      "content": "안녕하세요! 무엇을 도와드릴까요?",
      "error": null
    }
  ]
}
```

실패/만료/취소 시에는 `results` 대신 `errors` 필드가 포함됩니다.

콜백 전송에 실패하면 2초 간격으로 최대 3회 재시도하며, 모두 실패하면 상태가 `callback_failed`로 변경됩니다.

---

## 폴링 스케줄

배치 제출 후 APScheduler가 아래 간격으로 자동 폴링합니다.

| 시도 | 대기 시간 |
|------|----------|
| 1회 | 5분 |
| 2회 | 10분 |
| 3회 | 20분 |
| 4회 | 40분 |
| 5회~ | 60분 (고정) |

스케줄 정보는 Redis에 저장되므로 서버 재시작 시에도 유지됩니다.

---

## 로컬 개발

```bash
cd batch_server
pip install -r requirements.txt
uvicorn main:app --reload
```

테스트 서버 (콜백 수신 확인용):

```bash
cd test_request_server
uvicorn app:app --port 1818 --reload
```

`http://localhost:1818/sample/batch` — GET 요청으로 테스트 배치를 제출합니다.

---

## 프로젝트 구조

```
batch_server/
├── main.py                  # FastAPI 엔드포인트
├── worker.py                # 배치 제출 / 결과 처리 / 콜백 전송
├── batch_manager.py         # OpenAI Batch API 클라이언트
├── scheduler.py             # APScheduler 폴링 스케줄러
├── state_store.py           # Redis 상태 저장소
├── service_registry.py      # config.yaml 서비스 설정 로더
├── config.yaml              # 서비스 정의
├── Dockerfile
├── docker-compose.dev.yml   # dev 배포 설정 (포트 15000)
├── docker-compose.prod.yml  # prod 배포 설정 (포트 5000)
├── deploy.sh                # 빌드 + 배포 스크립트
└── requirements.txt

test_request_server/
└── app.py                   # 콜백 수신 테스트 서버
```
