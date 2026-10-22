import argparse
import csv
import json
from pathlib import Path

from workload.analyze_failover import stamp
from workload.client import connect


def summarize(cluster, clients, probe, verification, probe_rows):
    injection = next(e for e in cluster if e['event'] == 'disconnect_requested')
    confirmed = next(e for e in cluster if e['event'] == 'disconnect_confirmed')
    restored = next(e for e in cluster if e['event'] == 'network_restored')
    target = injection['target']
    samples = [e for e in cluster if e['event'] == 'snapshot']
    elected = next((e for e in samples if any(m['MEMBER_HOST'] != target and m['MEMBER_ROLE'] == 'PRIMARY'
        and m['MEMBER_STATE'] == 'ONLINE' for m in e['snapshot']['members'])), None)
    writes = [e for e in clients if e['event'] == 'commit_ack' and stamp(confirmed) <= stamp(e) < stamp(restored)]
    snapshots = [m for e in samples for m in e['snapshot']['members']]
    report = {'target': target, 'new_primary': next((m['MEMBER_HOST'] for m in elected['snapshot']['members']
              if m['MEMBER_ROLE'] == 'PRIMARY' and m['MEMBER_STATE'] == 'ONLINE'), None) if elected else None,
              'election_observed_seconds': (stamp(elected) - stamp(injection)).total_seconds() if elected else None,
              'application_recovery_observed_seconds': (min(stamp(e) for e in writes) - stamp(injection)).total_seconds() if writes else None,
              'majority_writes_during_partition': len(writes), 'isolated_probe_outcome': probe['outcome'],
              'isolated_probe_committed_after_restore': probe_rows,
              'isolated_offline_mode': probe['snapshot']['offline_mode'],
              'isolated_read_only': probe['snapshot']['read_only'],
              'isolated_super_read_only': probe['snapshot']['super_read_only'],
              **verification}
    local_members = probe['snapshot']['members']
    unreachable = sum(m['MEMBER_STATE'] == 'UNREACHABLE' for m in local_members) >= 2
    # A pending COMMIT is an observation of non-completion, not a proven rejection.
    restricted = probe['outcome'] == 'WRITE_REJECTED' or (probe['outcome'] == 'UNKNOWN' and probe['timed_out'] and unreachable and probe_rows == 0)
    report['data_integrity_passed'] = all(verification.get(key) == 0 for key in (
        'missing_acknowledged_requests', 'missing_confirmed_requests', 'duplicate_grants',
        'balance_mismatches', 'payload_mismatches', 'unexpected_grants'))
    report['isolated_write_restriction_observed'] = restricted
    report['passed'] = bool(verification['passed'] and elected and writes and restricted
                            and probe_rows == 0 and any(m['MEMBER_HOST'] != target for m in snapshots))
    return report


def main(run_id):
    if Path(run_id).name != run_id or run_id in ('.', '..') or '\\' in run_id:
        raise ValueError('Invalid run ID')
    directory = Path('results') / run_id
    cluster = [json.loads(line) for line in (directory / 'cluster-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    clients = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    probe = json.loads((directory / 'isolated-probe.json').read_text())
    verification = json.loads((directory / 'verification.json').read_text())
    with connect(admin=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM setup_checks WHERE check_id=%s', (probe['check_id'],))
            probe_rows = cursor.fetchone()[0]
    report = summarize(cluster, clients, probe, verification, probe_rows)
    (directory / 'partition-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    with (directory / 'partition-summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=report)
        writer.writeheader()
        writer.writerow(report)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    raise SystemExit(main(parser.parse_args().run_id))
