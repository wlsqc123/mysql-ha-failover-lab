# Primary 네트워크 격리 — 과반수 유지 실험

2026-10-22 현재 Primary의 Docker 네트워크 연결만 끊어 1 대 2 분할을 만들었다. 두 노드 쪽의 쓰기 재개, 격리 노드의 로컬 COMMIT 대기, 네트워크 복구 후 데이터 정합성을 실제로 확인했다.

**전체 결과는 실패다.** 데이터 유실·중복은 없었지만 요청 1건이 클라이언트 재시도 한도에 도달했다. 과반수의 서비스 재개와 애플리케이션의 모든 요청 완료는 별개의 기준으로 기록했다.

## 실험 방법

시작 상태는 mysql-3 PRIMARY, mysql-1/2 SECONDARY, ONLINE 3개였다. 보상 지급은 4개 작업자로 1,600건, 건당 100포인트를 요청했고 각 작업자는 요청 후 0.05초를 대기했다. 재시도는 동일 ID로 최대 40회, 시도 사이 0.5초였다.

```powershell
.\scripts\network_partition.ps1
```

스크립트는 현재 Primary와 네트워크를 실제 조회해 아래 동작을 수행한다.

```text
mysql-3 프로세스 유지 / Docker 네트워크 분리

[mysql-3 격리]       [mysql-1 ↔ mysql-2 ↔ Router]
 과반수 상실               과반수 유지
```

`docker network disconnect` 후 대상의 NetworkSettings.Networks가 빈 객체인 것을 확인했다. DB 간 통신과 Router 연결이 함께 끊어지는 방식이다. 단방향 패킷 손실이나 DB 간 통신만 선택적으로 끊는 실험은 아니다.

현재 Primary를 다시 확인한 뒤 분리하고, `finally`에서 원래 네트워크에 서비스명 alias를 붙여 재연결한다. 복구 시 강제 Primary 지정, Quorum 조작, 볼륨 삭제를 사용하지 않는다. 스크립트는 Windows PowerShell과 Docker에서 검증했다.

## 실제 결과

| 항목 | 결과 |
|---|---:|
| 격리 대상 | mysql-3, 프로세스 유지 |
| 두 노드 쪽 새 Primary | mysql-2 |
| 선출 관측 시간 | 24.258353초 |
| 첫 정상 쓰기 관측 시간 | 22.492081초 |
| 네트워크 분리 확인 후 복구 전 성공 응답 | 1,520건 |
| 전체 요청 | 1,600건 |
| COMMIT 응답 확인 / DB 지급 확인 | 1,599건 / 1,599건 |
| 오류 시도 / 재시도 | 104회 / 103회 |
| 최종 실패 요청 | 1건 |
| 보상 요청의 최종 결과 미확정 | 0건 |
| 승인 거래 누락 / 중복 / 잔액 불일치 | 모두 0건 |
| 전체 판정 | FAIL |
| 승인 거래와 지급 데이터 정합성 판정 | PASS |

시간은 네트워크 분리 명령 호출 직전부터 측정한 관측값이다. 선출 상태 조회에는 별도 컨테이너 실행·조회 간격이 포함돼 앱 쓰기 관측보다 늦었다. 내부 선출의 정확한 시각과 혼동하지 않는다. 1회 실험이며 별도 워밍업·반복·자원 통제를 하지 않았다.

실패한 요청은 `partition-20261022T093204Z-997be82b-80`이다. 40번째 연결 시도가 2003 오류로 끝난 뒤 FAILED로 기록됐고, 최종 DB에도 지급이 없었다. 나머지 요청은 성공했다. 이후 서비스가 복구됐다는 이유로 이 요청을 성공으로 바꾸거나, 기존 ID를 새 ID로 바꿔 다시 지급하지 않았다.

고정 횟수 재시도가 이번 분할의 복구 구간을 충분히 덮지 못했다. 이번 결과는 그대로 보존하며, 재시도 시간 예산·마감 시간과 실패 요청의 재처리 정책을 다음 개선 대상으로 남긴다. 한도를 변경한 재실험은 아직 수행하지 않았다.

## 격리 노드의 쓰기 제한 검사

Router 접속 실패만으로 Quorum 보호를 판단하지 않았다. 별도 Python 컨테이너를 격리 DB의 네트워크 namespace에 연결해 `127.0.0.1:3306`으로 접속했다. DB 프로세스는 계속 실행 중이었다. 제한된 lab_app 계정으로 별도 점검 테이블에 고유 ID를 INSERT하고 COMMIT을 시도했다. 보상 데이터와는 분리된 점검이다.

격리된 mysql-3에서 실제 관측한 값:

| 항목 | 값 |
|---|---|
| 자기 역할 | PRIMARY / ONLINE |
| mysql-1과 mysql-2 상태 | 둘 다 UNREACHABLE |
| read_only / super_read_only / offline_mode | 0 / 0 / 0 |
| group_replication_unreachable_majority_timeout | 0 |
| 로컬 접속과 INSERT | 실행됨 |
| COMMIT | 시작 기록은 있으나 8초 안에 성공 응답 없음 |
| 프로브 최초 분류 | UNKNOWN |
| 복구 후 점검 행 | 0건 |

8초 관측 제한 후 관리자 연결로 해당 세션에 KILL CONNECTION을 보내고 검사 프로세스를 종료했다. 이 시간 초과를 명시적 쓰기 거절이나 확정 롤백으로 분류하지 않았다. 네트워크 복구·재가입 후 DB 조회에서 미반영을 확인했다. 보상 요청의 UNKNOWN 0건과 이 별도 프로브의 초기 UNKNOWN은 다른 지표다.

MySQL 공식 문서는 소수 그룹이 Quorum을 확보하지 못하면 트랜잭션이 대기한다고 설명한다. 과반수 도달 불가 타임아웃 0은 자동 그룹 탈퇴를 무기한 기다리는 설정이다. 이번 관측은 이 설명과 일치한다. [소수 그룹의 트랜잭션 대기](https://dev.mysql.com/doc/refman/8.4/en/group-replication-responses-failure-partition.html), [관련 설정](https://dev.mysql.com/doc/refman/8.4/en/group-replication-system-variables.html).

따라서 “read_only=0이면 쓰기가 완료된다”거나 “PRIMARY라는 역할이 양쪽에 표시되면 양쪽에서 커밋할 수 있다”고 판단하면 안 된다. 이번에는 과반수 쪽의 성공 COMMIT과 소수 쪽의 미완료를 각각 관측했다. 모든 시점·모든 장애에서 무충돌을 보장한다는 주장은 하지 않는다.

## 네트워크 복구와 3노드 검증

재연결 후 mysql-3은 자동 재가입 첫 시도에 성공했다. mysql-1을 소스로 증분 분산 복구를 진행했고, mysql-2 PRIMARY와 mysql-1/3 SECONDARY로 ONLINE 3개가 됐다. 수동 재가입은 필요하지 않았다.

세 노드 각각에서 기준 GTID 적용과 요청 로그·지급 내역·잔액을 확인했다. 각 노드에 지급 1,599건이 존재했고 승인 거래 누락·중복·잔액 및 입력 불일치는 0건이었다. 검증기는 최종 실패 요청 1건 때문에 종료 코드 1을 반환했다. 원래 실행에서는 첫 노드의 종료 코드 1에서 후속 검사가 중단돼, 세 노드 검사와 집계를 별도로 완료했다. 이후 스크립트를 수정해 실패 보고서도 보존하면서 모든 노드를 검사하고 최종 실패를 반환하도록 했다. 수정 후 네트워크 분할을 다시 주입하지는 않았다.

복구 후 Router의 현재 Primary 연결, 점검 쓰기, 세 노드 반영은 통과했다.

## 원본과 한계

유효한 장애 실행: `results/partition-20261022T093204Z-997be82b/`

- `manifest.json`, `events.jsonl`, `verification-*.json`: 요청과 노드별 지급 검증
- `cluster-events.jsonl`, `isolated-networks.json`: 분리·복구와 과반수 멤버 상태
- `isolated-probe.json`, `isolated-probe.log`: localhost 접속, COMMIT 시도, 초기 UNKNOWN 및 세션 종료
- `reference.json`, `final-*.json`: 정상화 후 역할·GTID·복제 상태
- `partition-summary.json`, `partition-summary.csv`: 성공 기준과 실패 원인을 구분한 결과
- `services.log`, `workload.log`, `post-restore-smoke.log`: DB·Router·클라이언트 및 복구 후 점검

최초 실행 `partition-20261022T093131Z-48343f27`은 PowerShell의 네트워크명 추출 오류로 실제 분리 전에 중단했다. 해당 부하를 종료하고 무효 실행으로 표시했으며, 네트워크명 추출을 수정한 뒤 위의 유효 실험 1회를 수행했다. 기존 볼륨과 기록은 삭제하지 않았다.

회귀 테스트 32개와 Compose 설정·PowerShell 문법 검사를 통과했다. 신규 테스트는 COMMIT 시간 초과를 UNKNOWN으로 유지하고, 전송 오류를 Quorum 보호 근거로 오인하지 않으며, 데이터 정합성이 맞아도 최종 실패 요청이 있으면 전체 FAIL을 유지하는지 확인한다.

이번 범위는 한 노드를 격리한 1 대 2 분할이다. 전체 그룹이 과반수를 잃는 1 대 1 대 1 분할, 애플리케이션 재시도 정책 개선 후 재실험, 반복 통계는 미검증이다. 원본은 로컬에 보존하며 기본 Git 제외 대상이다.
