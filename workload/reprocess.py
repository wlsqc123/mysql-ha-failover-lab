"""Replay unconfirmed saved payloads into a new result directory; preserve the source."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from workload.runner import event_emitter, process_request
from workload.verify import verify


def select_pending(manifest, events):
    confirmed = {e['request_id'] for e in events if e['event'] in ('commit_ack', 'confirmed_existing')}
    return [request for request in manifest['requests'] if request['request_id'] not in confirmed]


def main(source, retry_budget):
    if not math.isfinite(retry_budget) or retry_budget <= 0:
        raise ValueError('retry_budget must be finite and positive')
    source = Path(source)
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    events = [json.loads(line) for line in (source / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    pending = select_pending(manifest, events)
    run_id = 'reprocess-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    directory = Path('results') / run_id
    directory.mkdir(parents=True)
    manifest = {**manifest, 'reprocess': {'run_id': run_id, 'source': str(source),
                'retry_budget': retry_budget, 'request_ids': [r['request_id'] for r in pending]}}
    with (directory / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    with (directory / 'events.jsonl').open('x', encoding='utf-8') as stream:
        for event in events:
            stream.write(json.dumps(event) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
        emit = event_emitter(stream, phase='reprocess')
        emit({'event': 'reprocess_started', 'source': str(source), 'selected_requests': len(pending)})
        for request in pending:
            process_request(request, emit, retry_budget=retry_budget)
    report = verify(directory)
    print(json.dumps({'directory': str(directory), 'reprocessed_requests': len(pending), **report}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--retry-budget', type=float, default=60)
    args = parser.parse_args()
    raise SystemExit(main(args.directory, args.retry_budget))
