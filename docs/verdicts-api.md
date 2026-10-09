# 판결 API와 처리 흐름

## 상황 확인 규칙

PATCH /api/cases/{case_id}/situation/ 은 summary, user_speaker_side만 받는다.
갈등 핵심(conflict_core), 관계, 원본 분석(ai_raw), 추가 설명은 수정할 수 없다.
summary는 최대 10,000자이며 판결 요청 후에는 수정할 수 없다.

1차 Model API는 사진과 관계를 분석하여 원문, 요약, 갈등 핵심, 불확실성을 저장한다.
POST /api/cases/{case_id}/judgment/ 는 사용자 확인을 확정한다.
2차에는 사진을 전달하지 않는다. 원래 AI 요약과 수정된 상황 분석, 화자 위치,
고정된 갈등 핵심, 저장된 대화와 불확실성을 전달한다. 추가 설명 입력은 없다.

## 최종 판결과 출처

2차 워커 내부 흐름:
1. Model API가 최대 4개의 후보 요소와 개인정보 없는 일반 검색어를 생성한다.
2. 요소별 Scholar Search 결과를 검색하고 Model API로 관련성을 검토한다.
3. 적합한 논문이 없는 요소만 Web Search를 수행하여 신뢰할 만한 기사인지 검토한다.
4. Model API가 전체 맥락과 선택된 자료를 바탕으로 최종 판결을 생성한다.
5. 제목, 입력 스냅샷, 양측 요소, 요소당 출처 하나, 추천 답장을 함께 저장한다.

검색 또는 참고 자료 검토 장애 시 출처 없이 판결을 생성할 수 있다.
검색 장애를 '논문 없음'으로 간주해 웹으로 대체하지 않는다.
자료 전체가 아닌 검색 설명을 참고하므로 원문을 읽었다고 표현하지 않는다.
URL/저자/제목은 모델이 생성하지 않고 검색 API 결과에서 가져온다.
논문은 일반 행동의 참고 기준이며 특정 책임 비율을 증명하는 자료가 아니다.

Liner 공식 문서:
- https://liner.com/developers/docs/liner-model-api-chat-completions
- https://liner.com/developers/docs/search-api

## 소유자 판결 조회

GET /api/verdicts/cases/{case_id}/ 는 소유자만 조회한다.
기존 GET /api/verdicts/requests/{request_id}/ 도 완료 시 verdict를 반환한다.

주요 필드:
- case_id, case_number (제00001호), relation, title
- fault_ratio: 본인 비율, other_fault_ratio: 상대 비율
- one_line, case_summary, judgment_text, recommended_reply
- self_factors, other_factors: 본인/상대 칩과 설명
- 요소의 evidence: 저장된 원문 목록의 0부터 시작하는 인덱스
- 요소의 source: 종류, 제목, URL, 출판처, 저자, 날짜, 참고 내용 설명
- can_expand: 출처가 있을 때만 true. false이면 참고 자료 펼치기 영역을 숨긴다.
- public_preview: 공유·배심원에게 보여줄 별도의 익명 제목과 상황 설명

SELF/OTHER는 사진의 LEFT/RIGHT와 다르다. 사용자 확인 화자 위치를 기준으로 판단한다.
한쪽에 실제 근거가 없으면 해당 칩 목록은 빈 배열일 수 있다.
추천 답장 복사, 저울 각도, 칩 표시와 상세 화면, 공유 모달은 프론트에서 구현한다.

## 공유

- POST /api/verdicts/cases/{case_id}/share/: 소유자만 공유 링크 생성, 반복 요청은 동일 링크
- GET /api/verdicts/shared/{token}/: 인증 없이 공개용 요약과 비율 조회
- DELETE /api/verdicts/cases/{case_id}/share/: 공유 링크 취소
- 취소 후 다시 생성하면 토큰이 바뀌어 이전 링크는 계속 무효
- 공유는 배심원 공개를 자동으로 실행하지 않는다.

공개 응답에는 사진, 원문, 입력 스냅샷, 개인용 판결문과 답장을 포함하지 않는다.
공개용 텍스트는 모델이 익명화한 결과이므로 프론트에서 public_preview를 보여주어
작성자가 공개할 내용을 확인할 수 있게 한다.
숨김·삭제 사건은 공개 링크로도 조회할 수 없다.

응답의 share_api_url은 JSON 조회 주소다.
FRONTEND_SHARE_BASE_URL=https://frontend.example.com/shared 를 설정하면
share_url은 {base}/{token} 화면 링크가 된다. 설정이 없으면 API 주소를 반환한다.
인스타그램/메신저 공유 및 이미지 저장은 프론트의 공유 기능으로 연결한다.

## 배심원 공개 요청

- POST /api/verdicts/cases/{case_id}/publish/: 소유자의 판결 완료 사건 공개 요청
- DELETE /api/verdicts/cases/{case_id}/publish/: 공개 취소

공개 요청은 Case.is_public과 public_at을 저장한다.
반복 공개 요청은 최초 public_at을 유지하고 코인을 추가 차감하지 않는다.
공개 취소 시 is_public=false, public_at=null로 변경한다.
공유 링크 생성과 배심원 공개는 독립적으로 동작한다.
과거 판결은 공개용 필드가 없으면 공유·공개할 수 없다.

jury 조회·투표 API와 라우팅은 이 변경에 포함하지 않는다.
jury 담당자가 공개된 사건을 is_public, public_at, is_hidden, deleted_at,
판결 완료 상태를 기준으로 조회하도록 연결하면 된다.
공개 요청 응답에는 case_id, is_public, 공개용 verdict만 반환하고
아직 구현되지 않은 jury API 주소는 반환하지 않는다.

## 실행

마이그레이션: python manage.py migrate
웹 서버: python manage.py runserver
별도 터미널 워커: python manage.py run_verdict_worker

SQLite 환경에서는 워커를 하나만 실행한다.
LINER_API_KEY는 기존 .env 키를 사용한다.
검색 설정: LINER_SEARCH_MAX_RESULTS(기본 3, 최대 5), LINER_SEARCH_TIMEOUT_SECONDS(기본 20).
모델 호출과 검색은 모두 기존 요청 제한 시간 안에서 수행되며 오래된 결과는 저장하지 않는다.
