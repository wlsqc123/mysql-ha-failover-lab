"""Read membership and replication diagnostics from one direct management connection."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from workload.client import connect


def observe(host, reference=None):
    with connect(admin=True, host=host, port=3306) as conn:
        with conn.cursor(dictionary=True) as cursor:
            if reference:
                gtid = json.loads(Path(reference).read_text(encoding='utf-8-sig'))['gtid_executed']
                cursor.execute('SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 10) AS wait_result', (gtid,))
                if cursor.fetchone()['wait_result'] != 0:
                    raise RuntimeError('Reference GTID was not applied within 10 seconds')
            cursor.execute('SELECT @@server_uuid AS server_uuid, @@read_only AS read_only, @@GLOBAL.gtid_executed AS gtid_executed')
            result = cursor.fetchone()
            if reference:
                cursor.execute('SELECT GTID_SUBSET(%s, @@GLOBAL.gtid_executed) AS applied', (gtid,))
                result['reference_gtid_applied'] = bool(cursor.fetchone()['applied'])
            cursor.execute('SELECT MEMBER_HOST, MEMBER_ID, MEMBER_STATE, MEMBER_ROLE FROM performance_schema.replication_group_members')
            result['members'] = cursor.fetchall()
            cursor.execute('SELECT CHANNEL_NAME, LAST_ERROR_NUMBER, LAST_ERROR_MESSAGE FROM performance_schema.replication_applier_status_by_worker WHERE LAST_ERROR_NUMBER <> 0')
            result['applier_errors'] = cursor.fetchall()
            cursor.execute('SELECT CHANNEL_NAME, SERVICE_STATE, RECEIVED_TRANSACTION_SET, LAST_ERROR_NUMBER, LAST_ERROR_MESSAGE FROM performance_schema.replication_connection_status')
            result['replication_connections'] = cursor.fetchall()
        result.update(observer=host, time_utc=datetime.now(timezone.utc).isoformat())
        return result


def assert_restored(snapshot, expected_primary):
    members = snapshot['members']
    if {m['MEMBER_HOST'] for m in members} != {'mysql-1', 'mysql-2', 'mysql-3'} or len(members) != 3:
        raise RuntimeError('Expected all three cluster members')
    if any(m['MEMBER_STATE'] != 'ONLINE' for m in members):
        raise RuntimeError('Not all members are ONLINE')
    primaries = [m['MEMBER_HOST'] for m in members if m['MEMBER_ROLE'] == 'PRIMARY']
    if primaries != [expected_primary]:
        raise RuntimeError('Primary changed or multiple primaries observed')
    local = next(m for m in members if m['MEMBER_HOST'] == snapshot['observer'])
    if bool(snapshot['read_only']) != (local['MEMBER_ROLE'] != 'PRIMARY'):
        raise RuntimeError('Local write protection does not match member role')
    if snapshot['applier_errors']:
        raise RuntimeError('Replication applier errors found')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('--reference')
    parser.add_argument('--require-primary')
    args = parser.parse_args()
    result = observe(args.host, args.reference)
    if args.require_primary:
        assert_restored(result, args.require_primary)
    print(json.dumps(result))
