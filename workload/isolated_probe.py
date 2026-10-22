"""Bounded local COMMIT probe inside the isolated DB's network namespace."""
import argparse
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import time
from uuid import uuid4

import mysql.connector

from workload.client import connect
from workload.observe import observe


def write_probe(pipe, check_id):
    conn = None
    def emit(event, **extra):
        pipe.send({'event': event, 'time_utc': datetime.now(timezone.utc).isoformat(), **extra})
    try:
        conn = connect()
        emit('connected', connection_id=conn.connection_id)
        conn.start_transaction()
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO setup_checks(check_id) VALUES (%s)', (check_id,))
        emit('commit_started')
        conn.commit()
        emit('commit_ack')
    except mysql.connector.Error as error:
        emit('db_error', errno=error.errno, error=str(error))
    finally:
        if conn:
            conn.close()
        pipe.close()


def classify(events, timed_out):
    if any(e['event'] == 'commit_ack' for e in events):
        return 'ACKNOWLEDGED'
    if timed_out or any(e['event'] == 'commit_started' for e in events):
        return 'UNKNOWN'
    # Authentication/transport/SQL syntax errors are not evidence of quorum protection.
    if any(e['event'] == 'db_error' and e['errno'] in (1290, 3032) for e in events):
        return 'WRITE_REJECTED'
    return 'INCONCLUSIVE'


def main(directory):
    snapshot = observe('127.0.0.1')
    check_id = str(uuid4())
    parent, child = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(target=write_probe, args=(child, check_id))
    events = []
    process.start()
    child.close()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process.is_alive():
        if parent.poll(0.1):
            try:
                events.append(parent.recv())
            except EOFError:
                break
    timed_out = process.is_alive()
    cleanup = None
    if timed_out:
        ids = [e['connection_id'] for e in events if e['event'] == 'connected']
        if ids:
            try:
                with connect(admin=True) as admin:
                    with admin.cursor() as cursor:
                        cursor.execute('KILL CONNECTION %s', (int(ids[0]),))
                cleanup = 'KILL CONNECTION sent'
            except mysql.connector.Error as error:
                cleanup = str(error)
        process.join(2)
        if process.is_alive():
            process.terminate()
    process.join(2)
    while parent.poll():
        try:
            events.append(parent.recv())
        except EOFError:
            break
    parent.close()
    report = {'snapshot': snapshot, 'check_id': check_id, 'timeout_seconds': 8,
              'timed_out': timed_out, 'cleanup': cleanup, 'events': events,
              'outcome': classify(events, timed_out)}
    (Path(directory) / 'isolated-probe.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    main(parser.parse_args().directory)
