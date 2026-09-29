# AI ERP 테스트베드

작고 정확한 주문·재고 ERP를 학습하고 검증하기 위한 독립 테스트베드입니다.
현재는 PostgreSQL, FastAPI 백엔드, Gemini API, 정적 웹 화면으로 구성되어 있습니다.

## 1. 목표

이 프로젝트의 목표는 회사 업무 전체를 한 번에 구현하는 것이 아니라, ERP의 핵심 흐름을 작게 검증하는 것입니다.

```text
상품 등록
  ↓
재고 보유
  ↓
주문 생성
  ↓
재고 차감
  ↓
주문·매출 요약
  ↓
Gemini AI 분석
```

현재 화면은 파일로 직접 열어 사용하는 방식입니다. 즉, `index.html`은 아직 Docker의 웹 서버가 제공하지 않습니다. 추후 Nginx 또는 별도 프론트엔드 서버를 추가할 예정입니다.

## 2. 디렉토리 구조

```text
테스트베드/
├── docker-compose.yml       PostgreSQL과 API 실행 설정
├── .env.example             Gemini 환경변수 예시
├── README.md                프로젝트 설명서
├── db/
│   └── init.sql              데이터베이스 초기 테이블과 샘플 데이터
├── backend/
│   ├── Dockerfile            API 이미지 생성 설정
│   ├── requirements.txt      Python 패키지 목록
│   └── app/
│       ├── __init__.py
│       └── main.py            FastAPI 애플리케이션
└── frontend/
    └── index.html             현재 사용하는 정적 웹 화면
```

## 3. 실행 구성

Docker Compose는 다음 두 컨테이너를 실행합니다.

### PostgreSQL

- 데이터베이스: `erp`
- 사용자: `erp_user`
- 포트: `5432`
- 초기화 파일: `db/init.sql`
- 데이터 보존 볼륨: `postgres_data`

### FastAPI

- 포트: `8000`
- API 문서: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/health
- 데이터베이스 연결: PostgreSQL 컨테이너
- Gemini 연결: Google Gemini Developer API

현재 프론트엔드는 Docker 서비스가 아니라 로컬 파일입니다.

```text
브라우저
  └── file:///.../frontend/index.html
          ↓
      FastAPI :8000
          ↓
      PostgreSQL :5432
```

## 4. 데이터베이스 구조

### products

상품과 현재 재고를 저장합니다.

- `id`: 상품 식별자
- `sku`: 상품 코드, 중복 불가
- `name`: 상품명
- `price`: 판매 가격
- `stock_quantity`: 현재 재고
- `created_at`: 등록 시각

### orders

주문 헤더를 저장합니다.

- `id`: 주문 식별자
- `customer_name`: 고객명
- `status`: 주문 상태
- `total_amount`: 주문 총액
- `created_at`: 주문 시각

### order_items

주문에 포함된 상품을 저장합니다.

- `order_id`: 주문 ID
- `product_id`: 상품 ID
- `quantity`: 주문 수량
- `unit_price`: 주문 시점의 단가

상품 가격이 나중에 변경되어도 과거 주문 금액을 보존하기 위해 주문 상품에 `unit_price`를 따로 저장합니다.

### audit_logs

중요한 작업의 기록을 저장합니다.

- 상품 생성
- 주문 생성
- 작업 유형
- 대상 엔터티
- 작업 상세 JSON
- 생성 시각

향후 사용자 ID, IP 주소, 변경 전·후 값 등을 추가할 수 있습니다.

## 5. 주문 처리 흐름

주문 API는 다음 순서로 처리됩니다.

1. 주문에 포함된 상품을 조회합니다.
2. `FOR UPDATE`로 해당 상품 행을 잠급니다.
3. 재고가 충분한지 확인합니다.
4. 주문 총액을 계산합니다.
5. 주문과 주문 상품을 저장합니다.
6. 재고를 차감합니다.
7. 감사 로그를 저장합니다.
8. 전체 작업을 커밋합니다.

중간에 오류가 발생하면 전체 작업을 롤백합니다. 따라서 주문만 저장되고 재고가 차감되지 않는 불일치 상황을 방지합니다.

## 6. 주요 API

### 상태 확인

```text
GET /health
```

### 상품 목록

```text
GET /api/products
```

### 상품 등록

```text
POST /api/products
Content-Type: application/json
```

```json
{
  "sku": "DEMO-003",
  "name": "테스트 모니터",
  "price": 180000,
  "stock_quantity": 10
}
```

### 주문 목록

```text
GET /api/orders
```

### 주문 생성

```text
POST /api/orders
Content-Type: application/json
```

```json
{
  "customer_name": "테스트 고객",
  "items": [
    {
      "product_id": 1,
      "quantity": 2
    }
  ]
}
```

### AI 상태

```text
GET /api/ai/status
```

### AI 질문

```text
POST /api/ai/ask
Content-Type: application/json
```

```json
{
  "question": "현재 재고가 가장 적은 상품은 무엇인가요?"
}
```

## 7. Gemini AI 연결 방식

AI가 PostgreSQL에 직접 접속하지 않습니다.

```text
사용자 질문
  ↓
FastAPI
  ↓
허용된 읽기 전용 데이터 조회
  ↓
상품·주문 요약 생성
  ↓
Gemini API
  ↓
자연어 응답
```

현재 Gemini에 전달하는 정보는 다음과 같습니다.

- 상품명
- 재고 수량
- 상품 가격
- 전체 주문 수
- 전체 주문 금액
- 최근 주문의 고객명·금액·상태·생성 시각

AI에는 임의 SQL 실행 권한을 주지 않았습니다. `DELETE`, `UPDATE`, `DROP` 같은 명령을 AI가 직접 실행하는 구조도 아닙니다.

## 8. 환경변수

`.env.example`을 복사해 `.env`를 만들고 API 키를 입력합니다.

```powershell
Copy-Item .env.example .env
```

예시:

```env
GEMINI_API_KEY=실제_Gemini_API_키
GEMINI_MODEL=gemini-3.5-flash
```

`.env`는 API 키가 포함되므로 Git에 커밋하면 안 됩니다.

## 9. 실행 방법

Docker Desktop을 실행한 후 테스트베드 디렉토리에서 실행합니다.

```powershell
cd "C:\Users\11211\Documents\GitHub\AI_ERP\테스트베드"
docker compose up --build -d
```

확인 주소:

- API 문서: http://localhost:8000/docs
- API 상태: http://localhost:8000/health
- 웹 화면: `frontend/index.html`을 브라우저에서 열기

## 10. 종료 방법

컨테이너만 종료하려면 다음 명령을 사용합니다.

```powershell
docker compose down
```

PostgreSQL 데이터까지 삭제하려면 별도로 볼륨을 삭제해야 합니다. 이 작업은 테스트 데이터가 사라지므로 주의해야 합니다.

```powershell
docker compose down -v
```

## 11. 현재 구현된 기능

- PostgreSQL 초기 스키마
- 샘플 상품 데이터
- 상품 등록
- 상품 및 재고 조회
- 주문 생성
- 주문 생성 시 재고 차감
- 재고 부족 시 주문 거부
- 트랜잭션 처리
- 상품·주문 감사 로그
- Gemini 기반 자연어 재고·주문 요약
- API 자동 문서화

## 12. 아직 구현하지 않은 기능

- 로그인
- 사용자별 역할과 권한
- 고객 테이블 분리
- 거래처 및 구매 관리
- 다중 창고
- 결제·환불
- 회계 처리
- 재고 입출고 이력
- 재고 조정 승인
- 웹 화면 Docker 제공
- 운영용 로그·모니터링
- 개인정보 마스킹

## 13. 향후 권장 순서

1. 사용자 로그인과 역할 기반 권한 추가
2. 재고 입출고 이력 테이블 추가
3. 고객·거래처 테이블 분리
4. 재고 부족 기준과 발주 추천 추가
5. 프론트엔드용 Nginx 컨테이너 추가
6. AI 질문별 허용 기능을 세분화
7. 감사 로그와 보안 로그 강화
8. 테스트 코드와 백업·복구 절차 추가

## 14. 보안 주의사항

- 실제 운영 데이터와 테스트 데이터를 분리합니다.
- `.env`와 API 키를 저장소에 올리지 않습니다.
- PostgreSQL 기본 비밀번호는 운영 환경에서 반드시 변경합니다.
- AI에 주민등록번호, 전화번호, 주소, 계좌번호 등을 보내지 않습니다.
- AI가 임의 SQL을 실행하지 못하도록 현재처럼 서버의 허용된 조회 함수만 사용합니다.
- 운영 환경에서는 CORS 허용 범위를 특정 도메인으로 제한합니다.
- 중요한 작업에는 사용자 인증, 권한 확인, 감사 로그를 적용합니다.
- 백업 데이터도 암호화하고 복구 테스트를 수행합니다.
