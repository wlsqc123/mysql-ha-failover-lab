# 두 번째 분할 실험의 연결 실패 조사

2026-10-22 추가 장애 없이 기존 로그, 현재 Docker 주소, DB 설정과 복구 계정의 노드 간 접속을 조사했다. 네트워크 분리·재시작·계정 변경·통신 스택 변경·버전 업그레이드는 하지 않았다.

**직접적인 실패 구간은 확인했다.** mysql-2를 격리한 동안 생존한 mysql-1/3이 서로의 도달성을 반복해서 잃고 회복하면서 새 Primary 선출을 진행하지 못했다. 현재의 지속적인 DNS 오류나 복구 계정 인증 실패는 발견하지 못했다. 당시 연결 실패를 일으킨 최하위 원인은 아직 확정하지 못했다.

## 당시 로그에서 확인한 사실

조사 대상은 `partition-20261022T114420Z-d3e2207d`이며 아래 시각은 UTC다. MySQL의 `performance_schema.error_log`와 저장된 services.log를 대조했다.

| 구간·노드 | 관측 |
|---|---|
| 11:44:25.474014 | mysql-2 네트워크 분리 명령 시작 |
| mysql-1 | 조사 구간의 MY-013780 연결 오류 8회, 과반수 도달 불가 메시지 8회 |
| mysql-3 | 조사 구간의 MY-013780 연결 오류 8회, 과반수 도달 불가 메시지 7회 |
| mysql-1/3 | 서로에 대한 UNREACHABLE 직후 reachable again 메시지를 반복 |
| 대기 기한 | 새 Primary 관측 없이 90초 대기 기한 도달 |
| 약 11:45:58~11:46:01 | mysql-2 재연결 후 과반수 접촉 복원 |
| 11:46:10 이후 수집 로그 | 같은 연결·도달성·Access denied 패턴 0건 |

MY-013780은 Group Replication의 MySQL 연결 수립 실패를 뜻한다. 하지만 당시 메시지에는 실패한 연결의 정확한 목적지 IP, TCP 오류 번호, TLS 처리 단계가 없었다. 로그에서 보이는 UNREACHABLE은 해당 노드의 장애 감지 판단이며, 그 자체가 실제 패킷 유실 위치를 증명하지는 않는다. [공식 오류 설명](https://dev.mysql.com/doc/mysql-errors/8.4/en/server-error-reference.html), [장애 감지 설명](https://dev.mysql.com/doc/refman/8.4/en/group-replication-failure-detection.html).

따라서 “두 프로세스가 살아 있다” 또는 “ONLINE으로 표시됐다”만으로 두 노드가 안정적으로 Quorum을 유지할 수 있다고 판정한 것은 부족했다. 클라이언트 재시도 예산을 늘려도 선출 자체가 진행되지 않는 구간을 해결할 수는 없다.

## 현재 주소와 설정

| 노드 | Docker IP | DB가 광고하는 그룹 주소 |
|---|---|---|
| mysql-1 | 172.23.0.4 | mysql-1:3306 |
| mysql-2 | 172.23.0.2 | mysql-2:3306 |
| mysql-3 | 172.23.0.3 | mysql-3:3306 |

세 노드에서 서비스명 DNS 응답은 위 주소와 일치했다. group_seeds에는 다른 두 노드가 들어 있었고 통신 스택 MYSQL, SSL REQUIRED, recovery_use_ssl ON이었다. member_expel_timeout은 5초, unreachable_majority_timeout은 0, auto-rejoin 시도는 3이었다. MySQL 호스트 캐시는 크기 0으로 비활성화돼 있었고 skip_name_resolve는 ON이었다.

서버 UUID와 @@hostname까지 확인해 TCP 연결이 다른 DB로 도착하지 않는지 검사했다. 이번 조사에서 현재 IP·DNS·광고 주소의 불일치는 없었다. 과거 분할 중 DNS 응답이나 내부 주소 캐시의 값을 기록한 것은 아니므로 과거 주소 문제까지 완전히 배제하지는 않는다.

## 실제 복구 계정의 6방향 인증 검사

각 DB 컨테이너의 네트워크 namespace에서 다른 두 DB에 접속했다. 진단용 별도 네트워크의 주소가 아니라 실제 DB와 같은 출발지 IP를 사용했다.

| 출발 → 도착 | TCP 연결 시간 | 저장된 복구 계정 인증·TLS |
|---|---:|---|
| mysql-1 → mysql-2 | 0.44ms | PASS |
| mysql-1 → mysql-3 | 0.92ms | PASS |
| mysql-2 → mysql-1 | 0.56ms | PASS |
| mysql-2 → mysql-3 | 0.53ms | PASS |
| mysql-3 → mysql-1 | 0.45ms | PASS |
| mysql-3 → mysql-2 | 0.92ms | PASS |

시간은 TCP 연결에 걸린 현재 단일 측정값이며 SQL·TLS 전체 처리 시간이나 당시 RTT가 아니다. root 접속과 별개로 각 노드에 저장된 실제 복구 계정으로 인증하고 목적지 hostname·UUID와 TLS cipher를 확인했다. mysql_innodb_cluster_1/2/3은 잠김·비밀번호 만료가 없었고 REPLICATION SLAVE, BACKUP_ADMIN, CONNECTION_ADMIN, GROUP_REPLICATION_STREAM 권한을 갖고 있었다.

복구 비밀번호는 DB에서 읽어 검사 프로세스 메모리 안에서만 사용했다. 출력·파일·커밋에 저장하지 않았고 오류 문구도 출력하지 않는다. 이 검사는 일반 MySQL 접속의 계정 인증과 TLS까지다. 실제 Group Replication 내부의 스트림 위임과 메시지 처리를 재현한 것은 아니므로 해당 경로까지 정상이라고 결론 내리지 않는다.

## 원인 후보 평가

| 후보 | 현재 평가 |
|---|---|
| 잘못된 서비스명 또는 현재 IP | 6방향 DNS·목적지 식별 검사에서 발견되지 않음 |
| 복구 계정의 지속적인 비밀번호·잠금·권한 문제 | 실제 인증·계정 상태·권한 검사에서 발견되지 않음 |
| 현재도 계속되는 노드 간 TCP/TLS 단절 | 이번 검사에서 발견되지 않았고 복구 후 관련 오류도 반복되지 않음 |
| 분할 중 Group Replication 내부 재연결 지연 | 당시 반복 연결 오류와 도달성 판단 변화로 조사 우선순위가 높음 |
| 공유 호스트의 순간 부하·Docker 패킷 문제 | 당시 자원 시계열과 패킷 캡처가 없어 미확정 |
| MySQL 버전의 통신 관련 문제 | 관련 수정 이력은 있으나 이번 원인으로 확정할 수 없음 |

MySQL 8.4.6은 과거 그룹 멤버십의 사라진 서버로 계속 재연결하는 과정이 일부 조건에서 그룹 통신 지연을 만들 수 있었던 문제를 수정했다(Bug #37704514). 기존 노드의 종료·재가입을 반복한 현재 실습 이력과 관련될 수 있는 **가설**이며, 해당 버그의 발생 조건을 입증하거나 수정 버전으로 비교 실험하지 않았다. [8.4.6 공식 수정 기록](https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-4-6.html).

반면 검색에서 증상이 비슷했던 SSL_connect 블로킹 문제(Bug #34348094, #36047891)는 이미 사용 중인 8.4.0의 수정 목록에 들어 있다. 증상이 비슷하다는 이유만으로 이 버그라고 판단하지 않았다. [8.4.0 공식 수정 기록](https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-4-0.html).

현재 Docker 상태에 OOMKilled는 없었고 별도의 초기 검증용 MySQL 3노드도 같은 호스트에 실행 중이었다. 이것은 부하 통제가 부족하다는 근거지만, 당시 CPU·메모리 지표가 없어 자원 부족이 원인이라고 주장하지 않는다. 이번 읽기 조사에서 다른 클러스터를 중지하지 않았다.

## 재현 가능한 현재 상태 검사

```powershell
.\scripts\diagnose_network.ps1
```

3개 DB가 실행 중이고 workload 이미지가 준비돼 있어야 한다. 현재 namespace의 DNS·TCP·root 목적지·복구 계정 인증·TLS를 확인하며, 각 노드에서 다른 두 노드로의 결과가 완전한지 검사한다. 현재 상태 검사를 통과해도 과거 근본 원인을 확정했다는 뜻은 아니다. 로그 조회 구간은 이 사건에 맞춰 고정했다.

진단 판정 단위 테스트 4개와 실제 6방향 접속 검사를 통과했다. 잘못된 목적지, TLS 누락, 상대 노드 누락을 성공으로 판정하지 않도록 확인했다. 별도 부하나 보상 지급을 실행하지 않았다.

원본: `results/diagnostics-20261022T120915Z/`의 노드별 JSON, docker-network.json, post-restore-services.log. 초기 수집본은 `results/diagnostics-20261022T120546Z/`, 실행 출력은 `results/network-diagnostics.log`에 보존했다. results 원본은 기본 Git 제외 대상이다.

## 다음 조사에서 필요한 증거

다음 장애 실험은 별도 단계로 진행해야 한다. 먼저 장애 구간의 Group Replication 상세 통신 로그, 모든 노드의 IP·DNS 응답 변화, TCP 재전송·TLS 연결 시도, 호스트 자원 시계열을 함께 수집하도록 준비한다. 특히 생존한 mysql-1/3 사이의 실제 패킷 실패인지, 내부 연결 처리 지연으로 인한 도달성 판단인지 구분해야 한다.

그다음 정상 초기 상태와 노드 쌍을 통제해 재현하고, 필요하면 지원되는 8.4 패치 버전으로 한 변수만 바꿔 비교한다. 이번 조사에서는 추가 장애, 통신 스택 변경 또는 업그레이드를 수행하지 않았다. 데이터와 모든 노드의 정상 실행 상태를 보존했다.
