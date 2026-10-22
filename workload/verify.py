"""Compare a saved request manifest and acknowledgements with one DB snapshot."""
import argparse
import json
from pathlib import Path

from workload.client import connect


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    events = []
    event_path = directory / 'events.jsonl'
    if event_path.exists():
        # A damaged or truncated log must fail visibly, never produce a passing report.
        events = [json.loads(line) for line in event_path.read_text(encoding='utf-8').splitlines()]
    requests = {r['request_id']: r for r in manifest['requests']}
    acknowledged = {e['request_id'] for e in events if e['event'] == 'commit_ack'}
    terminal = {e['request_id']: e['outcome'] for e in events if e['event'] == 'terminal'}
    committed = {}
    duplicate_grants = 0
    balance_mismatches = 0
    payload_mismatches = 0
    unexpected_grants = 0
    with connect(admin=True) as conn:
        conn.start_transaction(consistent_snapshot=True, isolation_level='REPEATABLE READ')
        with conn.cursor() as cursor:
            for account in manifest['accounts']:
                cursor.execute('SELECT request_id, account_id, amount FROM reward_grants WHERE account_id=%s', (account,))
                rows = cursor.fetchall()
                expected_balance = manifest['initial_balance']
                for request_id, account_id, amount in rows:
                    if request_id in committed:
                        duplicate_grants += 1
                    committed[request_id] = (account_id, amount)
                    expected = requests.get(request_id)
                    if expected is None:
                        unexpected_grants += 1
                    else:
                        expected_balance += expected['amount']
                        if (account_id, amount) != (expected['account_id'], expected['amount']):
                            payload_mismatches += 1
                cursor.execute('SELECT balance FROM accounts WHERE account_id=%s', (account,))
                row = cursor.fetchone()
                if row is None or row[0] != expected_balance:
                    balance_mismatches += 1
        conn.rollback()
    known_committed = set(committed) & set(requests)
    unresolved = {r for r in requests if r not in known_committed and terminal.get(r) != 'FAILED'}
    confirmed = {e['request_id'] for e in events if e['event'] in ('commit_ack', 'confirmed_existing')}
    report = {
        'total_requests': len(requests),
        'acknowledged_requests': len(acknowledged),
        'verified_committed_requests': len(known_committed),
        'unknown_outcome_requests': len(unresolved),
        'missing_acknowledged_requests': len(acknowledged - known_committed),
        'missing_confirmed_requests': len(confirmed - known_committed),
        'failed_requests': sum(r not in known_committed and terminal.get(r) == 'FAILED' for r in requests),
        'retry_count': sum(e['event'] == 'attempt' and e['attempt'] > 1 for e in events),
        'error_attempts': sum(e['event'] == 'attempt_error' for e in events),
        'duplicate_grants': duplicate_grants,
        'balance_mismatches': balance_mismatches,
        'payload_mismatches': payload_mismatches,
        'unexpected_grants': unexpected_grants,
    }
    report['passed'] = all(report[key] == 0 for key in (
        'unknown_outcome_requests', 'missing_acknowledged_requests', 'missing_confirmed_requests',
        'failed_requests', 'duplicate_grants', 'balance_mismatches', 'payload_mismatches', 'unexpected_grants'))
    (directory / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    result = verify(parser.parse_args().directory)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
