# 지속 요청과 클라이언트 기록

이번 단계는 장애 없이 지속적인 지급 요청을 보내고 요청 로그와 DB를 대조하는 데까지다.

## 동작

실행마다 별도 사용자 계정과 요청 ID를 만든다. 기존 데이터는 삭제하지 않는다. 기본값은 4개 작업자, 총 200개 요청, 건당 100포인트이며 각 작업자는 요청 후 0.05초 간격을 둔다. 횟수와 간격을 조절해 다음 단계의 장애 실험 중에도 요청을 지속할 수 있다.

지급 시작 전에 `manifest.json`에 사용자 초기 잔액 0과 모든 요청의 ID·사용자·금액을 저장한다. 요청별 시도, DB 오류, COMMIT 응답 확인, 기존 지급 조회 결과를 `events.jsonl`에 추가한다. 각 이벤트는 UTC 시각과 프로세스 시작 기준 경과 시간을 기록하고 flush/fsync한다. 로그 기록 비용이 포함되므로 순수 DB 처리량을 측정하는 도구는 아니다.

지급 함수는 매 호출마다 새 연결을 만든다. 현재 기본 정책은 같은 요청 ID와 입력으로 60초 동안 새 재시도를 허용하는 방식이다. 기존 횟수 모드는 `--max-attempts`로 명시한다. 인증 오류와 요청 입력 불일치는 재시도하지 않는다. 오류 코드는 명시적 목록을 사용한다. [시간 예산과 재처리의 실제 검증](retry-budget.md)에 진행 중인 DB 호출의 제한과 최신 실험 결과를 기록했다.

COMMIT 응답을 확인한 호출만 `commit_ack`로 기록한다. 재조회로 기존 지급을 확인한 경우는 `confirmed_existing`으로 구분한다. COMMIT 결과 불명확 오류가 한 번이라도 발생하고 끝내 확정하지 못하면 UNKNOWN을 유지한다. DB 커밋, 함수가 응답을 확인한 시점, 파일 기록 완료는 서로 다른 사건이다. 기록 시각을 서버의 실제 커밋 시각으로 해석하지 않는다. 프로세스가 COMMIT 후 기록 전에 종료되면 승인 로그에 남지 않을 수 있다.

## 독립 검증

모든 작업자가 종료한 뒤 별도 관리자 연결의 일관된 DB 스냅샷에서 실행 전용 계정들을 조회한다. 요청 manifest의 금액으로 기대 잔액을 계산하고 DB 잔액과 비교한다. 요청 ID, 사용자, 금액, 지급 건수와 응답 기록도 비교한다. 결과는 `verification.json`이다.

- acknowledged_requests: 파일에 COMMIT 응답 확인을 남긴 요청 수
- verified_committed_requests: 최종 DB에서 찾은 실행 대상 요청 수
- unknown_outcome_requests: DB에서 확인하지 못했고 확정 실패로도 분류하지 못한 요청 수
- missing_acknowledged_requests: 응답 확인 로그가 있지만 DB에는 없는 요청 수
- duplicate_grants / balance_mismatches / payload_mismatches: 중복·잔액·입력 불일치

UNKNOWN이 DB에서 발견되면 커밋으로 최종 분류한다. DB에 없는 UNKNOWN은 실패로 단정하지 않는다. 누락, 불일치, 해결되지 않은 UNKNOWN 또는 최종 실패가 남으면 검사 종료 코드는 1이다. 검증 시점 이후 상태까지 보장하지 않으며, 장애 중인 클러스터의 결과 확정은 이후 단계에서 다룬다. 로그가 깨졌거나 일부 줄이 잘리면 검사 자체를 오류로 종료한다.

## 실행

클러스터와 보상 테이블이 준비된 환경:

```powershell
.\scripts\lab.ps1 workload
```

요청 수를 늘리거나 저장된 결과를 다시 검사하려면:

```sh
docker compose run --rm workload python -m workload.runner --requests 1000 --workers 4 --interval 0.1
docker compose run --rm workload python -m workload.verify results/<run_id>
docker compose run --rm workload python -m unittest discover -s tests -p 'test_*.py' -v
```

실행 디렉터리는 결과에 출력된다. results는 호스트에 쓰기 가능한 별도 마운트로 저장하며 기본 Git 제외 대상이다. 테스트 컨테이너에는 결과 검증과 초기 사용자 생성에 필요한 관리자 자격 증명이 전달된다.

## 실제 정상 상태 검증

2026-10-22 기존 MySQL 8.4.0 클러스터와 Router에서 실행했다.

| 항목 | 결과 |
|---|---:|
| 요청 수 | 200 |
| COMMIT 응답 확인 기록 | 200 |
| DB에서 확인한 지급 | 200 |
| 결과 미확정 / 승인 거래 누락 | 0 / 0 |
| 최종 실패 / 재시도 | 0 / 0 |
| 중복 / 잔액 불일치 / 입력 불일치 | 0 / 0 / 0 |

원본: `results/20261022T082248Z-3210f55b/manifest.json`, `events.jsonl`, `verification.json`. 실행 출력은 `results/workload-baseline.log`에 있다.

연결 오류 후 같은 ID 재시도, COMMIT 결과 불명확 후 기존 지급 조회, UNKNOWN 유지, 입력 및 인증 오류의 재시도 제한은 모의 단위 테스트로 확인한다. 실제 DB 검증기의 실패 탐지 테스트에는 의도적으로 잘못된 승인 로그와 요청 금액을 사용한다. 이 테스트들은 실제 네트워크 단절 실험이나 COMMIT 응답 유실 실험의 완료를 의미하지 않는다.

Primary 종료, 자동 선출, 장애 중 쓰기 복구, 복구 시간 측정은 아직 수행하지 않았다.
