# mysql-ha-failover-lab

DBA 취업 포트폴리오용 MySQL 고가용성 및 장애 복구 검증 실습이다. Primary 장애 중 보상 지급을 지속하고, 승인 거래 보존과 중복 지급 여부를 검증하는 것을 목표로 한다.

현재는 **노드 장애·네트워크 분할과 재시도·재처리 검증을 진행한 단계**다. 네트워크 분할의 첫 실행은 재시도 한도 초과 1건으로 실패했고, 60초 정책 재실험도 선출 지연과 작업자 중단으로 실패했다. 복구 후 같은 ID 재처리로 두 실험의 지급을 각각 1,600건까지 완료했다. 자동 실험 실패와 재처리 성공을 구분하며, 전체 Quorum 상실은 후속 단계다.

```text
Workload Client → MySQL Router :6446 → InnoDB Cluster
                                      ├ mysql-1
                                      ├ mysql-2
                                      └ mysql-3
                                 PRIMARY 1 / SECONDARY 2
```

Server, Shell, Router는 8.4.0을 기준으로 고정한다. Oracle 공식 community-server 이미지에 포함된 Shell을 관리 도구로 사용한다. bootstrap은 Shell 8.4 여부를 확인한다. 최신 보안 버전을 의미하는 선택은 아니며 로컬 실습 전용이다.

- Docker Desktop의 Linux 컨테이너 또는 Linux Docker Engine
- Docker Compose v2 (`--wait` 지원), PowerShell 선택 사항
- 호스트 Python은 초기 구성에 필요하지 않음
- 이미지 다운로드와 3개 DB를 실행할 디스크·메모리 여유 필요

## 실행

Docker 엔진을 시작하고 이 폴더에서 `.env.example`을 `.env`로 복사한 뒤 실습용 비밀번호를 설정한다. `.env`는 커밋하지 않는다.

PowerShell:

```powershell
Copy-Item .env.example .env
.\scripts\lab.ps1 up
.\scripts\lab.ps1 status
.\scripts\lab.ps1 smoke
```

설정만 검사하려면 `.\tests\check_setup.ps1`을 실행한다. Docker 엔진 없이 실행 가능하며 DB 동작 검사는 포함하지 않는다.

Linux 또는 공통 Compose 명령:

```sh
cp .env.example .env
docker compose up -d --wait --wait-timeout 300 mysql-1 mysql-2 mysql-3
docker compose run --rm admin
docker compose up -d router
docker compose run --rm -e LAB_MODE=smoke admin
docker compose run --rm -e LAB_MODE=status admin
```

초기화는 실제 SQL 접속과 멤버 상태를 확인하며, 실패 시 0이 아닌 종료 코드를 반환한다. 재실행해도 기존 클러스터 메타데이터를 사용한다. 모든 노드가 중단된 클러스터를 새 클러스터로 재생성하지 않는다.

smoke는 Router 연결이 현재 쓰기 가능한 Primary에 도착하는지 확인하고 점검 행을 COMMIT한 뒤 재조회한다. 이어 각 노드에서 해당 GTID 적용을 기다리고 동일 행을 확인한다. 재시도 중 COMMIT 응답이 유실되면 점검 행이 남을 수 있다. 이는 보상 지급이나 멱등성 검증을 대신하지 않는다.

앱 연결: `127.0.0.1:6446`, 사용자 `lab_app`, DB `reward_lab`.
관리 연결: `127.0.0.1:3307`, `3308`, `3309`, 사용자 `root`.
최초 bootstrap의 시드가 mysql-1이라는 사실은 Primary 고정을 뜻하지 않는다.

```sh
docker compose logs --no-color --tail 200
docker compose stop
```

중지는 볼륨을 보존한다. 데이터 삭제 및 볼륨 초기화는 자동 실행하지 않는다. 비밀번호 변경 시 기존 DB 볼륨의 root 비밀번호는 환경변수만으로 변경되지 않는다.

## 결과 및 다음 단계

추가 장애 없이 두 번째 분할의 통신 문제를 조사했다. 현재 6방향 DNS·TCP·복구 계정 인증·TLS는 모두 통과했고, 복구 후 관련 오류 재발은 없었다. 당시 두 생존 노드의 Group Replication 연결 오류는 확인했지만 최하위 원인은 미확정이다. [진단 근거와 원인 후보](docs/network-diagnosis.md)에 현재 상태와 과거 원인 추정을 구분했다.

기본 클라이언트는 요청별 60초 재시도 예산을 사용하고 `python -m workload.reprocess results/<source_run>`으로 미완료 요청을 같은 ID로 처리한다. 60초 정책 재실험의 자동 완주는 실패했지만, 복구 후 재처리와 중복 재처리 검증은 통과했다. [시간 예산·추가 실패·재처리 결과](docs/retry-budget.md)에 원본을 보존한 비교와 한계를 기록했다.

Primary 네트워크 격리에서는 1,600건 중 1,599건이 성공하고 1건이 재시도 한도로 실패했다. 승인 거래 누락·중복·잔액 불일치는 0건이었다. 격리 노드의 로컬 COMMIT은 8초 내 완료되지 않았고, 복구 후 해당 점검 행은 없었다. 네트워크 복구 후 3노드 동기화와 Router 쓰기를 확인했다. [네트워크 분할 결과와 실패 분석](docs/network-partition.md)에 전체 FAIL과 정합성 PASS를 구분했다.

Secondary 강제 종료 실험도 1회 통과했다. mysql-3이 Primary를 유지했고 1,600건이 클라이언트 오류·재시도 없이 성공했다. 종료 노드를 복귀시킨 뒤 세 노드의 지급·잔액·기준 GTID 적용을 확인했다. [처리량·지연과 관측 한계](docs/secondary-failure.md)를 함께 기록했다.

Primary 강제 종료 실험은 1회 통과했다. 1,600건 지급 중 연결 실패 80회가 발생했고 같은 요청 ID 재시도로 모두 최종 성공했다. 앱 복구 관측 시간은 22.84초이며 승인 거래 누락·중복 지급·잔액 불일치는 0건이었다. [실험 조건과 결과](docs/primary-failover.md)에 측정 한계와 원본 파일 위치를 기록했다.

이후 mysql-1을 재시작해 약 24.01초 후 ONLINE Secondary로 자동 복귀한 것을 확인했다. mysql-3은 Primary를 유지했고, 3개 노드 각각에서 지급 1,600건과 잔액·기준 GTID 적용을 검증했다. [노드 복귀 결과](docs/node-rejoin.md)를 참고한다.

지속 요청 클라이언트는 `.\scripts\lab.ps1 workload`로 실행한다. 정상 상태에서 4개 작업자로 200건을 지급해 성공 응답 로그와 DB 기록이 모두 일치했고, 누락·중복·잔액 불일치는 0건이었다. [지속 요청과 기록 검증](docs/continuous-workload.md)에 실행 방법과 관측 한계를 정리했다.

보상 지급의 정상 동작과 중복 요청 검증도 구현했다. `.\scripts\lab.ps1 rewards`로 실행하며 신규 지급, 재요청, 입력 불일치, 동시 중복 요청, 원자적 롤백 등 7개 실제 DB 테스트를 통과했다. [보상 지급 설계와 결과](docs/reward-transactions.md)를 참고한다.

2026-10-22 로컬 Docker에서 초기 구성부터 노드 장애, 네트워크 분할·복구, 시간 예산과 미완료 요청 재처리까지 진행했다. [진행 상태](docs/experiments.md)에 검증 범위를 구분했다. 다음 과제는 두 번째 분할에서 나타난 생존 노드 간 연결 불안정 조사와 자동 완주 재검증이다.

단일 PC Docker 측정을 운영 SLA나 보장 가능한 RPO로 일반화하지 않는다. 자동 Failover는 백업을 대체하지 않는다. [설계와 한계](docs/architecture.md)를 참고한다.

공식 근거: [Oracle MySQL Docker 소스](https://github.com/mysql/mysql-docker/tree/main/mysql-server/8.4), [Router Docker 설치](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-installation-docker.html), [InnoDB Cluster](https://dev.mysql.com/doc/mysql-shell/8.4/en/mysql-innodb-cluster.html).
