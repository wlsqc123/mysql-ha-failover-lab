# 보상 지급 트랜잭션

이번 단계는 정상 상태에서의 원자성과 중복 지급 방지까지입니다. 장애 주입과 자동 재시도는 포함하지 않습니다.

## 설계

`accounts`는 사용자와 정수 잔액을, `reward_grants`는 요청 ID, 사용자, 정수 금액, GRANTED 상태와 지급 시각을 저장합니다. 금액은 소수점 없는 보상 포인트입니다. request_id는 대소문자를 구분하는 ASCII 기본키입니다. 사용자 외래키, 양수 금액과 음수가 아닌 잔액 제약을 둡니다.

클라이언트는 Router의 lab_app 계정으로 새 연결을 만들고 지급 내역 INSERT와 잔액 UPDATE를 한 트랜잭션으로 COMMIT합니다. 중간에 실패하면 두 변경을 모두 롤백합니다. 기본키가 UNIQUE도 보장하므로 중복 인덱스는 추가하지 않았습니다.

동일 요청이 들어오면 INSERT의 1062 중복키 오류를 구분합니다. 기존 트랜잭션을 롤백해 잠금과 스냅샷을 해제한 뒤 기존 지급을 조회합니다. 사용자·금액이 같으면 ALREADY_GRANTED를 반환하고 잔액은 수정하지 않습니다. 다르면 RequestMismatch 오류입니다. 앱은 기존 지급 내역에 UPDATE/DELETE 권한이 없습니다.

GRANTED는 이번 호출의 COMMIT 응답을 확인한 결과입니다. ALREADY_GRANTED는 이미 저장된 동일 지급을 조회한 결과이며, 이번 호출에서 새 COMMIT을 한 것은 아닙니다. COMMIT 호출에서 DB 오류가 나면 UnknownOutcome으로 구분합니다. 이 단계에서는 결과 불명확 상황의 재조회·재시도 자동화와 응답 유실 실험을 수행하지 않았습니다.

## 실행

초기 클러스터와 Router가 실행 중이어야 합니다.

```powershell
.\scripts\lab.ps1 rewards
```

공통 Compose 명령:

```sh
docker compose build workload
docker compose run --rm workload python -m workload.setup
docker compose run --rm workload
```

Python 3.12.8 컨테이너에 mysql-connector-python 9.1.0을 설치하므로 호스트 Python은 필요 없습니다. setup은 기존 테이블과 데이터를 보존하며 테이블 구조 변경을 자동 마이그레이션하지 않습니다. 테스트용 계정과 지급 기록도 삭제하지 않습니다. 테스트 초기 잔액은 0이며, 롤백 검증용 계정 하나는 의도적으로 BIGINT 최댓값으로 설정합니다. 따라서 전체 계정 잔액 합계를 무조건 지급 합계와 비교하는 용도로 이 테스트 데이터를 사용하면 안 됩니다.

테스트는 초기 계정 생성과 결과 조회에 관리자 권한을, 지급 처리에는 제한된 앱 권한을 사용합니다. 앱은 accounts SELECT/UPDATE, reward_grants SELECT/INSERT 권한을 사용합니다. 읽기 전용 호스트 볼륨과 빌드 제외 규칙을 적용했지만 workload 컨테이너는 로컬 테스트 도구이며 root 자격 증명을 전달받습니다.

## 실제 검증 결과

2026-10-22 기존 MySQL 8.4.0 3노드 클러스터의 Router를 경유해 7개 통합 테스트를 실행했고 모두 통과했습니다. 실행 시간은 0.980초로, 부하 성능 측정값은 아닙니다. 원본은 로컬 `results/reward-tests.log`에 저장했습니다.

| 검증 | 실제 결과 |
|---|---|
| 신규 100 지급 | 잔액 100 / 내역 1건 / 지급 합계 100 |
| 동일 요청 재실행 | ALREADY_GRANTED / 잔액과 내역 유지 |
| 동일 ID, 다른 금액 | RequestMismatch / 기존 지급 유지 |
| 동일 ID, 다른 사용자 | RequestMismatch / 다른 사용자 잔액 0 유지 |
| 같은 요청 8개 동시 실행 | GRANTED 1개, ALREADY_GRANTED 7개 / 잔액 100, 내역 1건 |
| 내역 INSERT 후 잔액 오버플로 | DB 오류 / 내역 0건 / 기존 잔액 유지 |
| 0·음수·bool·소수·범위 초과 금액 | 입력 오류 / 잔액 0, 내역 0건 |

테스트의 기대값은 요청 금액 100과 요청 횟수 등 입력에서 정합니다. 정상 지급·중복 지급은 잔액, 내역 건수, 내역 금액 합계를 함께 비교합니다. 장애 중 승인 거래 보존, 영속 클라이언트 로그, 재접속, 전체 실험 데이터 검증은 아직 범위 밖입니다.

드라이버 동작 근거: [트랜잭션 시작](https://dev.mysql.com/doc/connector-python/en/connector-python-api-mysqlconnection-start-transaction.html), [COMMIT](https://dev.mysql.com/doc/connector-python/en/connector-python-api-mysqlconnection-commit.html).
