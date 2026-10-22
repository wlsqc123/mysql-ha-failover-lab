# 실험 진행 상태

초기 설정 범위는 Phase 1이다. 2026-10-22 로컬 Docker에서 아래와 같이 실행했다.

| 항목 | 구현 | 실행 검증 |
|---|---|---|
| 3노드 및 독립 볼륨 | Compose 작성 | 통과: 3개 DB healthy |
| InnoDB Cluster 초기화 | AdminAPI 스크립트 작성 | 통과: rewardCluster / Single-Primary |
| PRIMARY 1개 / ONLINE 3개 확인 | 상태 조건 검사 작성 | 통과: mysql-1 PRIMARY, mysql-2/3 SECONDARY |
| Router 경유 COMMIT 및 재조회 | smoke 검사 작성 | 통과 |
| COMMIT 기록의 3노드 반영 | GTID 대기 및 노드별 직접 조회 | 통과 |
| 초기화 재실행 | 기존 메타데이터 재사용 | 통과 |
| 보상 지급 / 동일 요청 중복 방지 / 원자성 | Python 트랜잭션과 7개 통합 테스트 | 정상 상태 통과: [상세](reward-transactions.md) |
| 장애 중 승인 거래 보존 및 전체 데이터 검증 | 후속 Phase 2 | 미구현 |
| Primary 장애 및 쓰기 재개 | 후속 Phase 2 | 미구현 |
| Secondary / 분할 / 재가입 / 결과 불명확 | 후속 Phase 3 | 미구현 |

실행 환경: Windows Docker Desktop 4.25.0 / Linux x86_64 Engine 24.0.6 / Compose 2.23.0. Server, Shell, Router 8.4.0을 사용했다. 초기 엔진 연결 실패 후 Docker Desktop을 시작하여 실행했다.

`tests/check_setup.ps1`의 Compose 해석, 버전 고정, 독립 볼륨, 로컬 포트 바인딩, GTID, 재시작 정책, PowerShell 문법 검사도 통과했다.

최종 smoke 결과:

```json
{"result":"PASS","check_id":"2dfe5531-cdea-11f1-924e-0242ac170004","primary_uuid":"90cef3ce-cde9-11f1-95b7-0242ac170004","verified_nodes":["mysql-1","mysql-2","mysql-3"]}
```

원본 결과는 로컬 `results/phase1-smoke.json`, `results/phase1-status.log`, `results/phase1-repeat-up.log`, `results/phase1-validated-up.log`에 저장했다. results의 원본 로그는 기본 Git 제외 대상이다.

실행 중 발견해 수정한 사항:

- Shell 버전은 `Ver 8.4.0 ...` 형태이므로 검사식을 수정했다.
- `configureInstance()`에서 지원하지 않는 `interactive` 옵션을 제거했다.
- Shell 반환 배열의 `some/filter` 사용 대신 인덱스 순회로 멤버 상태를 검사했다.
- clone 후 mysqld가 exit 16으로 종료됐으며 자동 재시작 비활성화로 대기했다. 초기 빈 노드 가입은 증분 복구로 변경했다. 중단된 mysql-2를 시작하고, 미완료 가입의 Group Replication을 정지한 뒤 다시 가입시켰다. mysql-3의 최초 가입은 증분 복구로 완료했다. 기존 볼륨은 삭제하지 않았다.
- 초기화 재실행과 강화한 smoke를 연속 실행해 최종 통과를 확인했다.

Shell의 로케일 경고가 남아 있으나 명령 종료 코드는 0이며 SQL 검사는 통과했다. 전체 노드가 새 볼륨인 상태에서 수정본의 최초 구축을 다시 검증하지는 않았다.
실측 TPS, 복구 시간, 장애 중 데이터 유실 수치는 아직 없다. 이번 점검 행 복제 성공을 보상 지급 정합성 또는 장애 중 무손실 성과로 해석하지 않는다.

다음 단계에서는 보상 업무 모델과 독립 기대값 검증을 구현하고, 정상 상태와 Primary 장애를 초기 상태별 최소 5회 반복한다.
