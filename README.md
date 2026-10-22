# mysql-ha-failover-lab

DBA 취업 포트폴리오용 MySQL 고가용성 및 장애 복구 검증 실습이다. Primary 장애 중 보상 지급을 지속하고, 승인 거래 보존과 중복 지급 여부를 검증하는 것을 목표로 한다.

핵심 검증 범위는 **Primary 장애 전환, Secondary 장애, 장애 노드 복귀와 보상 지급의 중복 방지**다. 실제 DB에 장애를 주입하고 클라이언트 기록과 각 노드의 지급 내역·잔액을 대조했다. 아래 수치는 2026-10-22 단일 PC Docker 환경에서 관측한 결과다.

```text
Workload Client → MySQL Router :6446 → InnoDB Cluster
                                      ├ mysql-1
                                      ├ mysql-2
                                      └ mysql-3
                                 PRIMARY 1 / SECONDARY 2
```

## 구성 이유

Primary 1개와 Secondary 2개로 구성했다. 한 노드가 중단돼도 서로 통신 가능한 나머지 두 노드가 과반수를 구성하며, 기존 Primary가 종료되면 승격된 Secondary와 승격되지 않은 Secondary를 함께 확인할 수 있다. 역할은 실행 중 바뀌므로 장애 대상과 현재 Primary는 DB에서 조회한다.

## 핵심 검증 결과

| 검증 | 실제 결과 | 근거 |
|---|---|---|
| Primary 강제 종료 | 1,600건 최종 성공, 오류 시도 80회는 같은 ID로 재시도, 앱 쓰기 재개 관측 22.84초 | [장애 전환](docs/primary-failover.md) |
| 종료 노드 복귀 | 약 24.01초 후 ONLINE Secondary로 자동 복귀, 기존 Primary 유지 | [복귀·동기화](docs/node-rejoin.md) |
| Secondary 강제 종료·복귀 | 1,600건 성공, 클라이언트 오류·재시도 0회, 기존 Primary 유지 | [Secondary 장애](docs/secondary-failure.md) |
| 보상 지급 중복 방지·원자성 | 신규 지급, 재요청, 입력 불일치, 동시 중복 요청, 롤백 등 실제 DB 테스트 7개 통과 | [트랜잭션 검증](docs/reward-transactions.md) |
| 정상 상태 지속 요청 | 4개 작업자, 200건의 성공 기록과 DB 지급 일치 | [지속 요청](docs/continuous-workload.md) |

Primary·Secondary 장애 실험에서 승인 거래 누락, 중복 지급, 잔액 불일치는 모두 0건이었다. 장애 노드 복귀 후에는 **3개 노드 각각에서 지급 1,600건과 잔액, 기준 GTID 적용**을 확인했다. ONLINE 상태 확인과 실제 데이터 검증을 별도로 수행했다.

지급 요청은 고유 요청 ID를 사용한다. 같은 ID를 재시도해도 지급 내역의 유일성 제약으로 중복을 막고, 지급 내역과 잔액 변경은 하나의 트랜잭션으로 처리한다. 검증기는 입력 manifest와 클라이언트 로그를 DB 내역·잔액과 대조한다.

장애 실험은 각 1회의 절차 검증이며 복구 시간은 Docker 명령 처리와 관측 간격을 포함한다. Secondary 장애에서 오류가 없었다는 결과가 지연이나 처리 공백이 전혀 없었다는 뜻은 아니다.

## 실행 환경

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

## 추가 실험과 미해결 과제

네트워크 분할은 핵심 검증 완료 범위에서 제외했다. 최초 실험의 재시도 한도 초과와 다음 실험의 간헐적 선출 실패가 있었고, 추가 실험에서는 성공과 도달성 이상을 모두 관측했다. 원래 선출 실패의 근본 원인은 아직 확정하지 못했다. [분할 실험 기록](docs/network-partition.md)과 [추가 조사](docs/network-diagnosis.md)에 실패와 측정 한계를 보존했다.

미완료 요청을 같은 ID로 재처리해 두 실험의 지급을 각각 1,600건까지 완료했고, 같은 요청을 다시 처리해도 추가 지급이 없음을 확인했다. 이 결과를 네트워크 분할 대응의 전체 성공으로 해석하지 않는다. [재시도 정책과 재처리 결과](docs/retry-budget.md)를 참고한다.

전체 Quorum 상실과 의도적인 COMMIT 응답 유실은 미검증이다. [전체 진행 기록](docs/experiments.md)에 완료 범위와 추가 조사를 구분했다.

## 검증 한계

단일 PC Docker 측정을 운영 SLA나 보장 가능한 RPO로 일반화하지 않는다. 자동 Failover는 백업을 대체하지 않는다. [설계와 한계](docs/architecture.md)를 참고한다.

공식 근거: [Oracle MySQL Docker 소스](https://github.com/mysql/mysql-docker/tree/main/mysql-server/8.4), [Router Docker 설치](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-installation-docker.html), [InnoDB Cluster](https://dev.mysql.com/doc/mysql-shell/8.4/en/mysql-innodb-cluster.html).
