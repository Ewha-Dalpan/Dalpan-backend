# AWS 배포 준비

아직 인프라 생성/배포는 하지 않았다. 로컬은 SQLite와 private_media를 사용한다.
.env.example를 참고하여 기존 .env에 필요한 항목만 추가한다.

## DB와 비공개 이미지

운영에서는 DATABASE_URL로 PostgreSQL(RDS 등)을 연결한다.
기존 SQLite 데이터를 보존해야 한다면 백업 후 별도 이관과 코인 잔액 검증이 필요하다.
운영 설정은 SQLite, DEBUG=True, 빈 ALLOWED_HOSTS, 로컬 이미지 저장소를 거부한다.

CASE_STORAGE_BACKEND=s3와 AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME을 설정한다.
S3의 모든 퍼블릭 액세스를 차단하고 공개 버킷 정책/ACL을 사용하지 않는다.
웹과 워커에 같은 버킷과 IAM 역할을 부여한다.
역할에는 해당 버킷의 필요한 GetObject/PutObject/DeleteObject 권한만 부여한다.
file_overwrite=False의 존재 확인과 없는 파일의 404 처리에 필요한 ListBucket 권한도 추가한다.
키를 커밋하지 않는다. 암호화와 백업을 설정하고 버전 관리 시 삭제 버전 보관도 정한다.
이미지는 서버에서 읽어 인증된 소유자에게만 제공한다.
private_media나 S3 버킷을 정적 파일 경로로 공개하지 않는다.
기존 로컬 업로드가 있다면 비공개 저장소로 별도 이관해야 한다.

## 웹 서버와 워커

Linux 배포 환경에서 requirements.txt를 설치한 뒤:

~~~sh
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn config.wsgi:application --bind 0.0.0.0:8000
~~~

별도 프로세스로 동일 코드/환경변수/DB/저장소를 사용하는 워커를 실행한다:

~~~sh
python manage.py run_verdict_worker
~~~

현재 워커는 하나만 운영한다. 다중 워커 작업 선점은 별도 검증이 필요하다.
systemd 또는 ECS 서비스로 웹/워커 각각 종료 시 재시작하고 로그를 수집한다.
워커가 없으면 PENDING 요청이 쌓이며 시간 초과 시 환불된다.
순차 처리라 대기열이 길어지면 접수 시각부터 계산하는 제한 시간도 확인한다.

## 도메인과 HTTPS

프론트 도메인 확정 후 CORS_ALLOWED_ORIGINS와 FRONTEND_SHARE_BASE_URL을 설정한다.
ALLOWED_HOSTS는 백엔드 도메인, CSRF_TRUSTED_ORIGINS는 필요한 HTTPS 출처다.
운영에서는 SECURE_SSL_REDIRECT, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE를 True로 한다.
신뢰할 프록시에서 HTTPS를 종료할 때만 TRUST_PROXY_HTTPS_HEADER=True를 사용한다.
프록시가 외부 X-Forwarded-Proto를 신뢰하지 않고 실제 프로토콜로 설정하도록 구성한다.
HSTS는 HTTPS 검증 후 SECURE_HSTS_SECONDS를 작은 값부터 설정한다.
collectstatic 결과는 Nginx 등으로 제공하며 업로드와 경로를 분리한다.
프록시 요청 크기는 전체 이미지 20MiB에 multipart 여유분을 더해 제한한다.

## 보관 기간과 정기 작업

CASE_IMAGE_RETENTION_DAYS=0은 삭제 비활성화다. 정책 확정 후 양수로 설정한다.
기간이 지난 삭제 사건 또는 최신 요청이 취소된 미완료 사건만 정리한다.
진행/확인 대기 사건과 삭제되지 않은 완료 사건은 보존한다.
이미지 정리는 OCR 텍스트와 판결 입력 스냅샷을 삭제하지 않는다.
전체 데이터 보존/삭제 정책은 별도로 정해야 한다.

~~~sh
python manage.py fail_stale_verdicts
python manage.py cleanup_case_images
python manage.py cleanup_case_images --apply
~~~

정리는 기본 미리보기이며 --apply에서 실제 삭제한다.
만료/정리 명령을 EventBridge/ECS 스케줄이나 cron에 등록한다.
실패한 파일은 행을 보존하고 실패 종료하여 다음 실행에 재시도한다.
실제 스케줄 연결은 AWS 구성 시 해야 한다.

## 프론트 이미지 조회 변경

images[].image_key는 저장소 URL 대신 인증된
/api/cases/{case_id}/images/{image_id}/ 주소다.
프론트는 JWT Authorization 헤더로 fetch 후 Blob URL로 표시한다.
img src만으로는 JWT 헤더를 보낼 수 없다. Blob URL은 사용 후 해제한다.
공유 API에 원본 대화 이미지는 포함되지 않는다.

## 배포 후 확인

업로드 → 워커 분석 → 상황 확인 → 판결 생성 → 공유 조회를 실제 환경에서 확인한다.
비로그인/다른 계정의 이미지 조회 거부와 S3 공개 접근 거부를 확인한다.
실패 환불, 중복 차감, 재시작 후 데이터 유지와 DB 백업/복구를 확인한다.
로그 알림과 워커/대기열 모니터링을 구성한다.

저장소 설정 참고: https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html
