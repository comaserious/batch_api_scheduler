# OpenAI Batch Scheduler

## 1) 이 시스템이 하는 일

이 서비스는 OpenAI 대량 작업(Batch)을 **자동으로 처리해 주는 중간 서버**입니다.

- 여러 요청을 한 번에 OpenAI로 보냅니다.
- 작업이 끝났는지 주기적으로 자동 확인합니다.
- 완료되면 미리 등록한 주소로 결과를 자동 전달합니다(콜백).

쉽게 말해, 사람이 계속 상태를 확인하지 않아도 되는 **자동 처리 도우미**입니다.

---

## 2) 언제 쓰면 좋은가요?

- 챗봇 답변을 한꺼번에 생성해야 할 때
- 텍스트 임베딩을 대량으로 만들어야 할 때
- 이미지 생성 요청을 묶어서 처리할 때
- 처리 완료 결과를 다른 시스템(DB 저장 서비스 등)으로 자동 전달하고 싶을 때

---

## 3) 전체 흐름 (한눈에 보기)

1. 요청자가 이 서버에 배치 작업을 등록합니다.
2. 서버가 OpenAI Batch API에 작업을 전달합니다.
3. 서버가 시간이 지날 때마다 자동으로 완료 여부를 확인합니다.
4. 완료되면 결과를 등록된 콜백 URL로 전송합니다.
5. 실패/취소/만료 상태도 함께 전달됩니다.

---

## 4) 지원하는 작업 종류

- `responses`: 일반 텍스트 응답 생성
- `chat`: 대화형 응답 생성
- `embedding`: 텍스트를 벡터로 변환
- `images`: 이미지 생성

운영 관점에서는 “어떤 종류의 AI 작업을 보낼지”만 고르면 됩니다.

---

## 5) 시작 전에 준비할 것

### 필수 준비물

- OpenAI API Key
- Redis 실행 환경
- 결과를 받을 콜백 URL

### 환경 변수 예시 (`.env`)

```env
OPENAI_API_KEY=sk-...
REDIS_URL=redis://redis:6379
```

### 서비스 등록 예시 (`config.yaml`)

```yaml
services:
  my_service:
    callback_url: http://my-service/api/batch-result
    default_model: gpt-4.1
    default_type: responses
```

`my_service`는 서비스 이름입니다. 팀/프로젝트 단위로 구분해서 등록하면 관리가 쉽습니다.

---

## 6) 실제 사용 순서

## 6-1. 배치 등록

- API: `POST /batches`
- 역할: “이 작업들을 OpenAI에 맡겨 주세요” 요청

요청이 접수되면 `batch_id`가 반환됩니다. 이 값으로 상태를 조회하거나 취소할 수 있습니다.

## 6-2. 상태 확인

- API: `GET /batches/{batch_id}`
- 역할: 현재 상태 확인

주요 상태 예시:
- `validating`: 검증 중
- `in_progress`: 처리 중
- `completed`: 완료
- `failed`: 실패
- `cancelled`: 취소됨
- `callback_failed`: 결과 전달(콜백) 실패

## 6-3. 목록 조회

- API: `GET /batches?status=...`
- 역할: 상태별 배치 목록 확인

## 6-4. 취소/삭제

- API: `DELETE /batches/{batch_id}`
- 역할: OpenAI 작업 취소 + 저장 상태 정리

---

## 7) 결과는 어떻게 받나요? (콜백)

작업이 완료되면 `config.yaml`에 등록한 `callback_url`로 결과가 전송됩니다.

포인트:
- 성공 시: `results`에 실제 결과가 들어옵니다.
- 실패/만료/취소 시: `errors` 정보가 전달됩니다.
- 콜백 전송 실패 시: 2초 간격으로 최대 3번 재시도합니다.
- 재시도도 모두 실패하면 상태가 `callback_failed`가 됩니다.

---

## 8) 자동 확인(폴링) 주기

배치 등록 후 자동 확인 간격:

- 1회: 5분 후
- 2회: 10분 후
- 3회: 20분 후
- 4회: 40분 후
- 5회 이후: 60분 간격 고정

즉, 처음엔 빠르게 확인하고, 시간이 길어질수록 간격을 늘려 안정적으로 운영합니다.

---

## 9) 배포 운영 가이드 (중요)

핵심 원칙: **개발(dev)에서 검증한 동일 이미지를 운영(prod)에 그대로 배포**합니다.

권장 순서:

1. 버전 이미지 1회 빌드
2. dev 배포 후 기능 확인
3. 재빌드 없이 같은 이미지로 prod 배포 (`--no-build`)

이 방식의 장점:
- dev/prod 차이로 인한 사고 감소
- 장애 시 버전만 바꿔 빠른 롤백 가능
- 배포 이력 관리가 쉬움

---

## 10) 빠른 실행 예시

### 로컬 실행

```bash
cd batch_server
pip install -r requirements.txt
uvicorn main:app --reload
```

### 테스트 콜백 서버 실행

```bash
cd test_request_server
uvicorn app:app --port 1818 --reload
```

테스트 엔드포인트:
- `http://localhost:1818/sample/batch` (GET)

---

## 11) 실무 체크리스트

운영 전 체크:
- [ ] `OPENAI_API_KEY` 설정 확인
- [ ] Redis 연결 확인
- [ ] `config.yaml`의 `callback_url` 정상 동작 확인
- [ ] dev에서 실제 배치/콜백 성공 확인
- [ ] prod 배포는 `--no-build` 사용 확인

장애 대응 체크:
- [ ] 상태가 `failed`인지 `callback_failed`인지 구분
- [ ] 콜백 수신 시스템 로그 확인
- [ ] 필요 시 이전 버전 이미지로 즉시 롤백

---

## 12) 용어 간단 정리

- 배치(Batch): 요청 여러 건을 묶어서 한 번에 처리하는 방식
- 콜백(Callback): 작업 완료 시 결과를 특정 URL로 자동 전달하는 방식
- 폴링(Polling): 일정 시간마다 완료 여부를 확인하는 방식
- 롤백(Rollback): 문제가 생겼을 때 이전 정상 버전으로 되돌리는 작업

---

원본 기술 문서(`README.md`)는 개발자용 상세 레퍼런스입니다.  
이 문서(`B_readme.md`)는 운영/기획/비개발자 관점의 빠른 이해용 안내서입니다.
