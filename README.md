# mysql-ha-failover-lab

DBA 취업 포트폴리오용 MySQL 고가용성 및 장애 복구 검증 실습이다. Primary 장애 중 보상 지급을 지속하고, 승인 거래 보존과 중복 지급 여부를 검증하는 것을 목표로 한다.

현재는 **정상 동작과 Primary 장애 실험 1회 검증 완료 단계**다. Router 경유 지급 중 현재 Primary를 강제 종료하고 자동 선출, 쓰기 재개, 승인 기록 보존을 확인했다. 장애 노드 복귀와 반복 실험은 후속 단계다.

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

Primary 강제 종료 실험은 1회 통과했다. 1,600건 지급 중 연결 실패 80회가 발생했고 같은 요청 ID 재시도로 모두 최종 성공했다. 앱 복구 관측 시간은 22.84초이며 승인 거래 누락·중복 지급·잔액 불일치는 0건이었다. [실험 조건과 결과](docs/primary-failover.md)에 측정 한계와 원본 파일 위치를 기록했다. 현재 종료 노드는 중단 상태로 보존하고 있다.

지속 요청 클라이언트는 `.\scripts\lab.ps1 workload`로 실행한다. 정상 상태에서 4개 작업자로 200건을 지급해 성공 응답 로그와 DB 기록이 모두 일치했고, 누락·중복·잔액 불일치는 0건이었다. [지속 요청과 기록 검증](docs/continuous-workload.md)에 실행 방법과 관측 한계를 정리했다.

보상 지급의 정상 동작과 중복 요청 검증도 구현했다. `.\scripts\lab.ps1 rewards`로 실행하며 신규 지급, 재요청, 입력 불일치, 동시 중복 요청, 원자적 롤백 등 7개 실제 DB 테스트를 통과했다. [보상 지급 설계와 결과](docs/reward-transactions.md)를 참고한다.

2026-10-22 로컬 Docker에서 초기 구성부터 Primary 장애 실험까지 진행했다. [진행 상태](docs/experiments.md)에 검증 범위를 구분했다. 다음 단계는 종료 노드 복귀와 재가입·동기화 검증이다.

단일 PC Docker 측정을 운영 SLA나 보장 가능한 RPO로 일반화하지 않는다. 자동 Failover는 백업을 대체하지 않는다. [설계와 한계](docs/architecture.md)를 참고한다.

공식 근거: [Oracle MySQL Docker 소스](https://github.com/mysql/mysql-docker/tree/main/mysql-server/8.4), [Router Docker 설치](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-installation-docker.html), [InnoDB Cluster](https://dev.mysql.com/doc/mysql-shell/8.4/en/mysql-innodb-cluster.html).
