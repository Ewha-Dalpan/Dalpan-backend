# 사건 접수 및 상황 확인 API

모든 API는 로그인 필수이며 본인의 사건만 접근할 수 있습니다.

## 최초 접수

사진·관계는 접수 전까지 프론트에서 보관합니다. 톨 부족 시 충전 화면을 다녀온 뒤
POST /api/cases/ 한 번으로 접수합니다. 실제 잔액은 서버가 접수 시 검증합니다.
결제 창에서 톨을 충전하는 것만으로는 사건이 생성되지 않으며 접수 비용 차감이 성공해야 저장됩니다.
사건/요청 ID는 트랜잭션 내부에서 생성되지만 차감 또는 업로드 실패 시 DB 기록이 모두 롤백됩니다.
이미지 파일은 차감 이후 저장하고, 실패 시 저장된 파일도 정리합니다.
DB 커밋이 성공한 뒤 AI 작업을 등록합니다. AI 작업 등록 실패는 기존 fail_request를 통해 환불합니다.

성공 응답(201):

```json
{
  "case_id": 123,
  "verdict_request": {"id": 456, "case_id": 123, "stage": "ANALYSIS", "status": "PENDING"}
}
```

verdict_request에는 시작/종료 시각 등 상태 조회 필드도 포함됩니다.
잔액 부족은 400, AI 미연결은 503이며 이 경우 새 사건과 사진은 저장되지 않습니다.

| 요청 | 역할 |
| --- | --- |
| GET /api/coins/balance/ | 사건 생성 전 보유 톨 확인 |
| POST /api/cases/ | multipart images 1~6장, relation(필수). 사건 저장·1톨 차감·분석 요청을 한 번에 처리 |
| GET /api/cases/{id}/payment/ | balance, cost, can_submit 확인. 실제 잔액 검증은 접수 시 다시 수행 |
| POST /api/cases/{id}/submit/ | 분석 실패로 환불된 기존 사건 재접수. 진행 중이면 기존 요청 반환 |
| GET /api/verdicts/requests/{id}/ | 요청 상태 조회. 처리 중 타임아웃 시 실패 및 환불 |
| GET /api/cases/{id}/ | 저장된 이미지, 상황, 화자 위치, 수정 불가 conflict_core 조회 |
| PATCH /api/cases/{id}/situation/ | summary 및 user_speaker_side(LEFT/RIGHT) 수정 및 즉시 저장 |
| GET /api/cases/?unfinished=true | 마이페이지 판결 전 사건 목록(페이지네이션) |
| POST /api/cases/{id}/judgment/ | 확인 완료 후 최종 판결 요청. 접수 때 낸 톨 추가 차감 없음 |

화면에서 나가기를 누르기 전에 PATCH로 마지막 수정 내용을 저장하고, 성공 응답 후 이동합니다.
이어하기는 같은 사건의 GET 응답으로 화면을 복원합니다. 별도 나가기 API는 필요하지 않습니다.
계속하기/나가기 팝업 자체는 프론트엔드에서 구현합니다. 새로 시작 시 기존 사건 종료 기능은 이번 범위에 포함하지 않습니다.

## 상태와 AI 연결

Case: 최초 접수 성공 시 CONFIRMING(분석 중/상황 확인 중) → JUDGING → JUDGED.
상황 분석 실패는 WRITING, 최종 판결 실패는 READY로 돌아가며 기존 fail_request에서 환불합니다.
VerdictRequest: ANALYSIS/PENDING → AWAITING_CONFIRMATION → JUDGMENT/PENDING → DONE.
AWAITING_CONFIRMATION은 사용자 확인 대기이므로 자동 타임아웃 및 환불 대상이 아닙니다.
최종 판결 시작 시 started_at을 갱신하여 타임아웃을 다시 계산합니다.
기존 사건 재접수 및 판결의 중복 클릭은 진행 중인 요청을 반환합니다.
새 사건 생성 POST는 매번 새 사건을 생성하므로 프론트는 요청 중 접수 버튼을 비활성화해야 합니다.

AI API가 없으므로 현재 접수/판결 호출은 503이며 톨은 차감하지 않습니다.
AI 담당자가 설정에 다음 두 dotted path를 연결하면 됩니다:

```python
VERDICT_ANALYSIS_DISPATCHER = 'your_tasks.enqueue_analysis'
VERDICT_JUDGMENT_DISPATCHER = 'your_tasks.enqueue_judgment'
```

두 함수는 request_id를 받아 작업 큐에 등록하는 함수입니다.
등록은 transaction.on_commit에서 실행하며 실제 LLM 호출은 워커가 담당합니다.
워커는 요청의 stage/status를 확인하고, 이미지/상황을 조회하여 작업합니다.
분석 성공은 complete_analysis(request_id, summary=..., conflict_core=..., user_speaker_side='RIGHT'),
판결 성공은 기존 complete_request(...), 실패는 기존 fail_request(request_id, reason)만 호출합니다.
분석 결과의 화자 위치를 생략하면 RIGHT가 기본값입니다.
사용자의 수정 내용을 판결 입력으로 사용하고, 완료 콜백 외에서 직접 DB 상태를 바꾸지 마세요.

AI 큐 등록 실패는 환불됩니다. 프로세스 종료 등으로 콜백이 유실되는 경우를 위해 기존
fail_stale_verdicts 관리 명령을 주기적으로 실행해야 합니다.
현재 SQLite는 select_for_update 행 잠금을 지원하지 않으므로 운영 동시 요청 보장은
행 잠금을 지원하는 DB에서 확인해야 합니다.

현재 작업 전부터 VerdictReply.tone은 모델과 기존 마이그레이션 사이에 차이가 있습니다.
이번 마이그레이션에서는 해당 필드를 삭제하지 않습니다. 별도 변경으로 정리하세요.
