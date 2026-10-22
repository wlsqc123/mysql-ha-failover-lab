"""Summarize one real injection; all times are observation times."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path


def stamp(event):
    return datetime.fromisoformat(event['time_utc'].replace('Z', '+00:00'))


def summarize(cluster_events, client_events, verification):
    injection = next(e for e in cluster_events if e['event'] == 'kill_requested')
    confirmed = next(e for e in cluster_events if e['event'] == 'kill_confirmed')
    target = injection['target']
    samples = [e for e in cluster_events if e['event'] == 'snapshot' and stamp(e) >= stamp(confirmed)]
    detected = next((e for e in samples if not any(
        m['MEMBER_HOST'] == target and m['MEMBER_STATE'] == 'ONLINE' for m in e['snapshot']['members'])), None)
    elected = next((e for e in samples if any(
        m['MEMBER_HOST'] != target and m['MEMBER_STATE'] == 'ONLINE' and m['MEMBER_ROLE'] == 'PRIMARY'
        for m in e['snapshot']['members'])), None)
    # A confirmed existing grant is not a new successful write.
    writes = [e for e in client_events if e['event'] == 'commit_ack' and stamp(e) >= stamp(confirmed)]
    recovered = min(writes, key=stamp) if writes else None
    def elapsed(event):
        return (stamp(event) - stamp(injection)).total_seconds() if event else None
    acknowledgements_before = sum(e['event'] == 'commit_ack' and stamp(e) < stamp(injection) for e in client_events)
    result = {
        'target': target,
        'injection_requested_utc': stamp(injection).isoformat(),
        'injection_confirmed_utc': stamp(confirmed).isoformat(),
        'failure_detected_observed_utc': stamp(detected).isoformat() if detected else None,
        'primary_elected_observed_utc': stamp(elected).isoformat() if elected else None,
        'first_write_success_observed_utc': stamp(recovered).isoformat() if recovered else None,
        'new_primary': next((m['MEMBER_HOST'] for m in elected['snapshot']['members']
                             if m['MEMBER_ROLE'] == 'PRIMARY' and m['MEMBER_STATE'] == 'ONLINE'), None) if elected else None,
        'acknowledgements_before_fault': acknowledgements_before,
        'failure_detection_observed_seconds': elapsed(detected),
        'primary_election_observed_seconds': elapsed(elected),
        'application_recovery_observed_seconds': elapsed(recovered),
        'injection_command_seconds': (stamp(confirmed) - stamp(injection)).total_seconds(),
        'failed_attempts_after_injection': sum(e['event'] == 'attempt_error' and stamp(e) >= stamp(injection) for e in client_events),
        'unknown_commit_attempts': sum(e['event'] == 'attempt_error' and e.get('unknown_commit', False) for e in client_events),
        **verification,
    }
    result['passed'] = bool(verification['passed'] and detected and elected and recovered and acknowledgements_before)
    return result


def main(run_id):
    # Only a directory name is accepted; never read arbitrary paths supplied as a run ID.
    if Path(run_id).name != run_id or '/' in run_id or '\\' in run_id or run_id in ('.', '..'):
        raise ValueError('Invalid run ID')
    directory = Path('results') / run_id
    cluster_events = [json.loads(line) for line in (directory / 'cluster-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    client_events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    verification = json.loads((directory / 'verification.json').read_text())
    report = summarize(cluster_events, client_events, verification)
    (directory / 'failover-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    with (directory / 'failover-summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=report.keys())
        writer.writeheader()
        writer.writerow(report)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    raise SystemExit(main(parser.parse_args().run_id))
