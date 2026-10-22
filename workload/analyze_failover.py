"""Summarize one real injection; all times are observation times."""
import argparse
import csv
from datetime import datetime, timedelta
import json
import math
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


def summarize_secondary(cluster_events, client_events, verification):
    baseline = next(e for e in cluster_events if e['event'] == 'baseline')
    injection = next(e for e in cluster_events if e['event'] == 'kill_requested')
    confirmed = next(e for e in cluster_events if e['event'] == 'kill_confirmed')
    primary = baseline['initial_primary']
    target = injection['target']
    samples = [e for e in cluster_events if e['event'] == 'snapshot' and stamp(e) >= stamp(confirmed)]
    def primary_unchanged(event):
        return [m['MEMBER_HOST'] for m in event['snapshot']['members']
                if m['MEMBER_ROLE'] == 'PRIMARY' and m['MEMBER_STATE'] == 'ONLINE'] == [primary]
    detected = next((e for e in samples if not any(
        m['MEMBER_HOST'] == target and m['MEMBER_STATE'] == 'ONLINE' for m in e['snapshot']['members'])), None)
    two_online = any(sum(m['MEMBER_STATE'] == 'ONLINE' for m in e['snapshot']['members']) == 2 for e in samples)
    acknowledgements = [e for e in client_events if e['event'] == 'commit_ack']
    after = [e for e in acknowledgements if stamp(e) >= stamp(confirmed)]
    attempts = {}
    for event in client_events:
        if event['event'] == 'attempt':
            attempts.setdefault(event['request_id'], event)
    # End-to-end latency includes all attempts and retry waits, but excludes the next-request interval.
    def window_metrics(start, end):
        successes = [e for e in acknowledgements if start <= stamp(e) < end]
        latencies = sorted((stamp(e) - stamp(attempts[e['request_id']])).total_seconds() * 1000
                           for e in successes)
        duration = (end - start).total_seconds()
        return {'duration_seconds': duration, 'acknowledged_requests': len(successes),
                'successful_requests_per_second': len(successes) / duration if duration > 0 else None,
                'p50_latency_ms': latencies[math.ceil(len(latencies) * 0.50) - 1] if latencies else None,
                'p95_latency_ms': latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
                'p99_latency_ms': latencies[math.ceil(len(latencies) * 0.99) - 1] if latencies else None,
                'max_latency_ms': latencies[-1] if latencies else None}
    start = min((stamp(e) for e in attempts.values()), default=stamp(injection))
    end = max((stamp(e) for e in acknowledgements), default=stamp(confirmed))
    # Include the final acknowledgement in the observation window.
    end += timedelta(microseconds=1)
    ordered_acks = sorted(acknowledgements, key=stamp)
    gaps = [(stamp(right) - stamp(left)).total_seconds()
            for left, right in zip(ordered_acks, ordered_acks[1:]) if stamp(right) >= stamp(injection)]
    maintained = bool(samples) and all(primary_unchanged(e) for e in samples)
    report = {
        'scenario': 'secondary', 'target': target, 'primary': primary,
        'primary_maintained_in_all_samples': maintained,
        'two_online_members_observed': two_online,
        'observation_errors': sum(e['event'] == 'observation_error' for e in cluster_events),
        'injection_requested_utc': stamp(injection).isoformat(),
        'injection_confirmed_utc': stamp(confirmed).isoformat(),
        'failure_detection_observed_seconds': (stamp(detected) - stamp(injection)).total_seconds() if detected else None,
        'first_write_after_fault_seconds': (min(stamp(e) for e in after) - stamp(injection)).total_seconds() if after else None,
        'max_ack_gap_after_fault_seconds': max(gaps) if gaps else None,
        'failed_attempts_after_injection': sum(e['event'] == 'attempt_error' and stamp(e) >= stamp(injection) for e in client_events),
        'before_fault': window_metrics(start, stamp(injection)),
        'after_fault_confirmation': window_metrics(stamp(confirmed), end),
        **verification,
    }
    report['passed'] = bool(verification['passed'] and maintained and two_online and detected and after)
    return report


def main(run_id):
    # Only a directory name is accepted; never read arbitrary paths supplied as a run ID.
    if Path(run_id).name != run_id or '/' in run_id or '\\' in run_id or run_id in ('.', '..'):
        raise ValueError('Invalid run ID')
    directory = Path('results') / run_id
    cluster_events = [json.loads(line) for line in (directory / 'cluster-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    client_events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    verification = json.loads((directory / 'verification.json').read_text())
    baseline = next(e for e in cluster_events if e['event'] == 'baseline')
    secondary = baseline.get('scenario') == 'secondary'
    report = (summarize_secondary if secondary else summarize)(cluster_events, client_events, verification)
    name = 'secondary-summary' if secondary else 'failover-summary'
    (directory / (name + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    flat = {}
    for key, value in report.items():
        if isinstance(value, dict):
            flat.update({key + '_' + nested: item for nested, item in value.items()})
        else:
            flat[key] = value
    with (directory / (name + '.csv')).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=flat.keys())
        writer.writeheader()
        writer.writerow(flat)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    raise SystemExit(main(parser.parse_args().run_id))
