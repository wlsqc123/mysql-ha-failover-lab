# 장애 노드 복귀와 동기화 검증

2026-10-22 Primary 장애 실험에서 종료한 mysql-1을 재시작했다. 볼륨을 유지한 채 자동 재가입과 증분 분산 복구가 진행됐고, 세 노드가 다시 ONLINE이 된 뒤 장애 실험의 지급 데이터를 검증했다.

## 실제 결과

| 항목 | 결과 |
|---|---|
| 복귀 전 mysql-1 | exit 137, 중단 상태 |
| 복귀 전 생존 노드 | mysql-3 PRIMARY, mysql-2 SECONDARY |
| 복귀 방식 | 기존 컨테이너 start 후 자동 재가입 |
| 복구 방식 | Incremental recovery from a group donor |
| 복구 소스 | mysql-2:3306, GTID 기반 복제 |
| 재시작 명령 시작부터 ONLINE 관측 | 24.0080095초 |
| 복귀 후 역할 | mysql-1 SECONDARY, read_only=1 |
| 기존 Primary | mysql-3 유지, read_only=0 |
| 모든 ONLINE 노드의 기준 GTID 적용 | 3개 노드 모두 확인 |
| 지급 데이터 | 3개 노드 각각에서 1,600건 확인 |
| 승인 거래 누락 / 중복 / 잔액 불일치 | 각 노드 모두 0건 |

실측 시간은 재시작 명령 호출부터 생존 노드 조회에서 ONLINE 3개를 확인한 시점까지다. Docker 처리와 주기적 조회 비용을 포함하며, 데이터 전체 검증이 끝날 때까지의 시간과는 다르다. 이번에는 보상 부하를 추가하지 않고 직전 장애 실험 데이터를 검증했다.

## 복구 근거

mysql-1의 재시작 로그에서 다음 흐름을 확인했다. 아래 시각은 UTC다.

- 08:43:41: crash recovery 완료 및 MySQL 접속 준비
- 08:43:46: mysql-3을 Primary로 하는 Secondary 역할 확인
- 08:43:46: `Distributed recovery will transfer data using: Incremental recovery from a group donor`
- 08:43:46: mysql-2의 복구 계정에 연결, GTID 기반 복제 시작
- 08:44:00: 복구 대상 GTID 도달, recovery 채널 정리
- 08:44:00: 그룹에서 ONLINE 선언

세 노드 모두에서 PRIMARY 1개, SECONDARY 2개, ONLINE 3개와 로컬 read_only 값, applier 오류 부재를 확인했다. 저장한 Primary의 기준 GTID를 각 노드에서 대기한 뒤 포함 여부를 확인했다. 서로 다른 시각의 GTID 문자열 일치를 동기화 완료 조건으로 사용하지 않았다.

각 노드의 직통 관리 연결에서 요청 manifest·성공 응답 로그와 지급 내역·잔액을 비교했다. 이는 노드가 ONLINE이라는 상태 확인과 별개의 데이터 검증이다.

## 실행

클러스터에 ONLINE 생존 노드가 두 개 있고 복귀 대상의 기존 컨테이너가 중단된 상태에서 실행한다. 현재는 이미 정상 복귀했으므로 같은 명령을 다시 실행하면 대상이 중단돼 있지 않다는 검사에서 종료된다.

```powershell
.\scripts\rejoin_node.ps1 -Node mysql-1 -Observer mysql-3 -WorkloadRun primary-20261022T083212Z-69043167
```

초기 상태와 기존 Primary를 기록하고 대상 컨테이너만 시작한다. 실제 멤버 상태를 조회해 최대 90초 동안 자동 재가입을 기다린다. 이후 각 노드의 역할, 읽기 전용 상태, 기준 GTID 적용, 지정된 실험 데이터의 정합성을 검사한다. 기존 데이터와 볼륨은 삭제하지 않는다.

자동 재가입에 실패하면 로그를 보존하고 오류로 종료한다. 강제 Quorum 조작, Primary 수동 지정, clone, 메타데이터 삭제를 자동 실행하지 않는다. 이번 실행은 자동 재가입에 성공했으므로 AdminAPI를 사용한 수동 재가입 경로는 구현·실행하지 않았다. 필요 시 정상 Primary와 메타데이터·GTID 상태를 확인한 뒤 공식 [rejoinInstance 절차](https://docs.oracle.com/cd/E17952_01/mysql-shell-8.4-en/rejoin-cluster.html)를 적용하는 별도 단계로 다룬다.

## 결과 파일과 남은 범위

로컬 디렉터리: `results/rejoin-20261022T084338Z-d3bcb937/`

- `before.json`, `events.jsonl`: 재시작 전 상태와 복귀 관측
- `recovery.log`: crash recovery, 증분 분산 복구, ONLINE 로그
- `reference.json`, `final-mysql-*.json`: 기준 GTID와 각 노드 적용·역할·복제 상태
- `verification-mysql-*.json`: 각 노드의 지급 정합성 결과
- `summary.json`: 자동 재가입 결과와 ONLINE 관측 시간
- `router-smoke.log`: 복귀 후 Router의 현재 Primary 연결 및 점검 쓰기 결과

복귀 판정 단위 테스트 4개도 통과했다. Secondary 상태를 유지한 정상 복귀는 허용하고, RECOVERING 상태·Primary 역할 역전·쓰기 가능한 Secondary는 완료로 판정하지 않도록 확인했다. 이번 결과는 단일 실험이며, 복귀 중 지속 부하와 증분 복구가 불가능한 경우의 대체 복구는 미검증이다.
