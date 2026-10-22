# 실험 진행 상태

2026-10-22 로컬 Docker에서 실행한 기록이다. 포트폴리오의 핵심 범위는 Primary·Secondary 장애, 노드 복귀와 보상 지급 검증이다. 네트워크 분할은 미해결 원인이 남은 추가 실험으로 구분한다.

## 핵심 검증

| 항목 | 구현 | 실행 검증 |
|---|---|---|
| 3노드 및 독립 볼륨 | Compose 작성 | 통과: 3개 DB healthy |
| InnoDB Cluster 초기화 | AdminAPI 스크립트 작성 | 통과: rewardCluster / Single-Primary |
| PRIMARY 1개 / ONLINE 3개 확인 | 상태 조건 검사 작성 | 초기 구축 시 통과: mysql-1 PRIMARY, mysql-2/3 SECONDARY. 이후 역할은 선출에 따라 변경 |
| Router 경유 COMMIT 및 재조회 | smoke 검사 작성 | 통과 |
| COMMIT 기록의 3노드 반영 | GTID 대기 및 노드별 직접 조회 | 통과 |
| 초기화 재실행 | 기존 메타데이터 재사용 | 통과 |
| 보상 지급 / 동일 요청 중복 방지 / 원자성 | Python 트랜잭션과 7개 통합 테스트 | 정상 상태 통과: [상세](reward-transactions.md) |
| 장애 중 승인 거래 보존 및 지급·잔액 검증 | 요청 로그와 DB 대조 | Primary 장애 1회 통과, 1,600건: [상세](primary-failover.md) |
| 지속 요청 및 로그·DB 대조 | 재시도 클라이언트와 독립 검증 | 정상 상태 200건 통과: [상세](continuous-workload.md) |
| Primary 장애 및 쓰기 재개 | 동적 대상 조회, SIGKILL, 관측 자동화 | 1회 통과, 앱 복구 관측 22.84초 |
| 장애 노드 재가입 및 동기화 | 자동 복귀 관측과 노드별 데이터 검증 | 1회 통과: [상세](node-rejoin.md) |
| Secondary 장애 및 복귀 | 동적 Secondary 조회, SIGKILL, 데이터·성능 관측 | 1회 통과, 1,600건, 오류·재시도 0회: [상세](secondary-failure.md) |

장애 실험 뒤 노드 복귀까지 확인했으며, 각 노드에서 지급 1,600건·잔액·기준 GTID 적용을 검사했다. 각 1회의 절차 검증이며 반복 성능 검증이나 운영 가용성 보장을 뜻하지 않는다.

## 추가 실험과 미검증 범위

아래 실험은 핵심 검증 완료 성과에 포함하지 않는다. 실패 기록과 복구 후 재처리 결과를 보존한다.

| 항목 | 구현 | 실행 검증 |
|---|---|---|
| 1 대 2 네트워크 분할 및 복구 | 실제 네트워크 단절·localhost COMMIT 검사 | 전체 FAIL: 재시도 한도 초과 1건, 정합성 PASS: [상세](network-partition.md) |
| 60초 재시도 정책의 분할 재실험 | 시간 예산 기반 클라이언트 | FAIL: 선출 지연·작업자 중단, 미해결 원인 있음: [상세](retry-budget.md) |
| 같은 ID 미완료 요청 재처리 | 원본 보존, 별도 결과·노드별 검증 | 두 실험 각각 최종 1,600건, 중복 재처리 추가 지급 0건 |
| 생존 노드 통신 실패 조사 | 로그·DNS·실제 계정 접속 검사와 추가 분할 재현 | 정상 상태 6방향 검사 PASS. 추가 실험에서 도달성 이상과 선출 성공을 관측했으나 원래 선출 실패의 근본 원인은 미확정: [상세](network-diagnosis.md) |
| 전체 Quorum 상실 / COMMIT 응답 유실 실험 | 후속 Phase 3 | 미구현 |

## 초기 구축 기록

이하 상태와 로그는 초기 구축 당시의 기록이며 현재 Primary를 나타내지 않는다.

실행 환경: Windows Docker Desktop 4.25.0 / Linux x86_64 Engine 24.0.6 / Compose 2.23.0. Server, Shell, Router 8.4.0을 사용했다. 초기 엔진 연결 실패 후 Docker Desktop을 시작하여 실행했다.

`tests/check_setup.ps1`의 Compose 해석, 버전 고정, 독립 볼륨, 로컬 포트 바인딩, GTID, 재시작 정책, PowerShell 문법 검사도 통과했다.

초기 구축 완료 시 smoke 결과:

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
이 초기 점검 행의 복제 성공만으로 보상 지급 정합성을 판단하지 않았다. 이후 보상 업무 모델과 독립 기대값 검증을 구현했고, 위의 핵심 검증에서 장애 중 지급 결과를 대조했다. 초기 상태를 통제한 최소 5회 반복 검증은 아직 수행하지 않았다.
