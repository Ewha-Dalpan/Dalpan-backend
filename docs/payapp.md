# PayApp 결제 전환

회원가입/로그인 절차와 신규 가입 보너스 1톨은 변경하지 않습니다.
상품별 가격은 서버 DB에서 결정합니다. 50톨 상품의 coin_amount=55는 보너스 포함 실제 지급량입니다.
마이그레이션은 이전 상품을 판매 중지하고 기존 주문/결제 기록을 보존합니다.

| code | 기본 | 보너스 | 지급 합계 | 원 |
| --- | ---: | ---: | ---: | ---: |
| tol-1 | 1 | 0 | 1 | 1000 |
| tol-5 | 5 | 0 | 5 | 4950 |
| tol-10 | 10 | 0 | 10 | 9900 |
| tol-20 | 20 | 0 | 20 | 19800 |
| tol-50 | 50 | 5 | 55 | 49500 |

1톨은 PayApp 최소 결제금액 1,000원에 맞추었습니다.

## 환경 설정과 배포

.env 및 배포 플랫폼에 다음을 실제 값으로 설정합니다.
PAYAPP_USER_ID, PAYAPP_LINK_KEY, PAYAPP_LINK_VALUE, PAYAPP_REQUEST_KEY,
PAYAPP_FEEDBACK_URL, PAYAPP_RETURN_URL.
비밀 값은 프론트에 전달하지 않습니다.
PAYAPP_FEEDBACK_URL=https://백엔드도메인/api/payments/payapp/feedback/
PAYAPP_RETURN_URL=https://프론트도메인/payments/result

백엔드: python manage.py migrate, python manage.py check 실행 후 배포.
통보 URL은 외부 HTTPS로 직접 POST 가능해야 하며 로그인/리다이렉션이 없어야 합니다.
이전 Paddle webhook과 비밀키는 기존 거래 환불/지연 통보 처리를 위해 유지합니다.
기존 webhook/ 경로는 Paddle용이고, PayApp에는 payapp/feedback/ 경로를 사용합니다.

## 프론트 변경 계약

GET /api/payments/products/ (공개)
기존 id/name/coin_amount/price_krw에 code/base_coin_amount/bonus_coin_amount 추가.
coin_amount는 이미 보너스를 포함하므로 bonus_coin_amount를 다시 더하지 않습니다.
가입 시 1톨은 구매 상품과 별도이며 결제 때마다 지급하지 않습니다.

POST /api/payments/orders/ (기존 JWT 로그인)
입력: {"product_id": 123, "recvphone": "01012345678"}
번호는 결제 시에만 입력받습니다. 회원가입 입력/사용자 모델 변경은 없습니다.
하이픈과 공백은 정규화합니다. 가격/코인 수량을 클라이언트에서 지정하지 않습니다.
헤더 Idempotency-Key: crypto.randomUUID() 권장. 같은 결제 시도 재전송에는 같은 키 사용.
결제 버튼을 요청 중 비활성화하고, 오류 시 자동으로 새 키를 생성해 주문을 반복하지 않습니다.

응답: {"order_id": "...", "provider": "payapp", "pay_url": "https://...",
       "coin_amount": 55, "price_krw": 49500, "status": "PENDING"}
Paddle.Checkout.open 대신 window.location.assign(data.pay_url) 사용.
프론트 저장소는 이 작업 공간에 없으므로 별도 프론트 반영이 필요합니다.

복귀 화면은 query의 order_id로 GET /api/payments/orders/{uuid}/ 조회.
PAID일 때만 완료 표시. PENDING은 확인 중, REFUNDED는 환불, CANCELLED는 요청취소,
EXPIRED는 만료 안내. 서버 통보는 복귀 화면과 별개로 올 수 있습니다.
몇 초 간격으로 최대 1~2분 조회하고 계속 대기면 지연 안내를 표시합니다.
GET 주문 상세는 소유자만 접근 가능합니다. pay_url은 소유자에게만 반환합니다.
상태 변경/코인 지급을 프론트 완료 화면에서 실행하지 않습니다.

## 실패와 환불

503: 설정 누락. 판매자 계정 연동정보와 공개 URL 설정 확인.
400: 입력 오류/최소금액 미만.
502: 원격 거절 또는 응답 불명. 주문번호로 상태/PayApp 판매자 화면 확인.
409: 같은 Idempotency-Key의 기존 주문 진행 중/확정 상태. 응답 order_id로 조회.
UNKNOWN 주문은 인증된 feedback이 도착하면 거래번호를 복구하고 지급 가능합니다.
응답 유실 및 통보 미수신 건은 판매자 화면의 var1 주문번호와 대조해야 합니다.
외부 결제 API에 대한 자동 재요청은 하지 않습니다.

전액 승인취소: 결제 때 지급한 보너스 포함 수량을 잔액 한도까지 회수.
부족분은 관리자 PayAppFeedback의 DATA_ERROR로 기록. 이미 사용한 코인은 수동 확인.
부분취소: DATA_ERROR 기록 후 관리자 수동 처리. 자동 비례회수는 하지 않습니다.
요청취소: 미결제 주문을 CANCELLED 처리. 가상계좌 입금대기는 지급하지 않습니다.
PayApp 판매자 화면에서 취소하며 이용자용 환불 API는 추가하지 않았습니다.
통보 로그에는 연동키/해시/전화번호/카드정보/주문 인증토큰을 저장하지 않습니다.

## 확인

python manage.py test payments coins accounts
결제 API 호출은 테스트에서 모킹하며 실제 승인/취소를 실행하지 않습니다.
운영 전 실제 계정/결제수단으로 결제와 취소를 확인해야 합니다.
공식 문서: https://www.payapp.kr/dev_center/dev_center01.html
