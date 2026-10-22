"""Bounded continuous workload. Every retry uses the original request payload."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import re
import threading
import time
from uuid import uuid4

import mysql.connector

from workload.client import MAX_BIGINT, UnknownOutcome, connect, grant_reward
from workload.verify import verify

RETRYABLE = {1040, 1042, 1047, 1158, 1159, 1160, 1161, 1205, 1213, 1290,
             2002, 2003, 2006, 2013, 2055}


def process_request(request, emit, max_attempts, retry_delay):
    uncertain = False
    for attempt in range(1, max_attempts + 1):
        emit({'event': 'attempt', 'request_id': request['request_id'], 'attempt': attempt})
        try:
            result = grant_reward(**request)
        except (UnknownOutcome, mysql.connector.Error) as error:
            uncertain = uncertain or isinstance(error, UnknownOutcome)
            retryable = isinstance(error, UnknownOutcome) or error.errno in RETRYABLE
            emit({'event': 'attempt_error', 'request_id': request['request_id'],
                  'attempt': attempt, 'unknown_commit': isinstance(error, UnknownOutcome),
                  'error': str(error), 'retryable': retryable})
            if not retryable or attempt == max_attempts:
                outcome = 'UNKNOWN' if uncertain else 'FAILED'
                emit({'event': 'terminal', 'request_id': request['request_id'], 'outcome': outcome})
                return outcome
            time.sleep(retry_delay)
        except ValueError as error:
            outcome = 'UNKNOWN' if uncertain else 'FAILED'
            emit({'event': 'terminal', 'request_id': request['request_id'],
                  'outcome': outcome, 'error': str(error)})
            return outcome
        else:
            # These timestamps observe the function return; they are not server commit times.
            emit({'event': 'commit_ack' if result == 'GRANTED' else 'confirmed_existing',
                  'request_id': request['request_id'], 'attempt': attempt})
            return result


def run(args):
    if min(args.requests, args.workers, args.max_attempts) < 1 or min(args.interval, args.retry_delay) < 0:
        raise ValueError('Counts must be positive; delays must be nonnegative')
    run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', run_id):
        raise ValueError('run-id must contain 1-48 ASCII letters, digits, _ or -')
    directory = Path('results') / run_id
    directory.mkdir(parents=True)
    accounts = [secrets.randbelow(MAX_BIGINT - 1) + 1 for _ in range(args.workers)]
    requests = [{'request_id': run_id + '-' + str(i), 'account_id': accounts[i % len(accounts)],
                 'amount': 100} for i in range(args.requests)]
    manifest = {'run_id': run_id, 'accounts': accounts, 'initial_balance': 0,
                'requests': requests, 'settings': vars(args)}
    with (directory / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    with connect(admin=True) as conn:
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO accounts(account_id) VALUES (%s)', [(a,) for a in accounts])
        conn.commit()
    lock = threading.Lock()
    started = time.monotonic()
    with (directory / 'events.jsonl').open('x', encoding='utf-8') as stream:
        def emit(event):
            with lock:
                event.update(time_utc=datetime.now(timezone.utc).isoformat(),
                             elapsed_seconds=time.monotonic() - started)
                stream.write(json.dumps(event) + '\n')
                stream.flush()
                os.fsync(stream.fileno())

        def worker(index):
            for request in requests[index::args.workers]:
                process_request(request, emit, args.max_attempts, args.retry_delay)
                if args.interval:
                    time.sleep(args.interval)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(worker, range(args.workers)))
    report = verify(directory)
    print(json.dumps({'directory': str(directory), **report}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--max-attempts', type=int, default=5)
    parser.add_argument('--retry-delay', type=float, default=0.5)
    parser.add_argument('--interval', type=float, default=0.05)
    parser.add_argument('--run-id')
    raise SystemExit(run(parser.parse_args()))
