"""Read-only peer probes; keep stored replication credentials only in memory."""
import argparse
from datetime import datetime, timezone
import json
import socket
import time

import mysql.connector

from workload.client import connect

HOSTS = ('mysql-1', 'mysql-2', 'mysql-3')
VARIABLES = ('group_replication_local_address', 'group_replication_group_seeds',
             'group_replication_communication_stack', 'group_replication_ssl_mode',
             'group_replication_recovery_use_ssl', 'group_replication_member_expel_timeout',
             'group_replication_unreachable_majority_timeout', 'group_replication_autorejoin_tries',
             'group_replication_exit_state_action', 'skip_name_resolve', 'host_cache_size')


def collect(source):
    report = {'source': source, 'time_utc': datetime.now(timezone.utc).isoformat(), 'peers': []}
    with connect(admin=True, host='127.0.0.1', port=3306) as local:
        with local.cursor(dictionary=True) as cursor:
            cursor.execute('SELECT @@server_uuid AS uuid, @@hostname AS hostname, @@read_only AS read_only')
            report['local'] = cursor.fetchone()
            cursor.execute('SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_variables WHERE VARIABLE_NAME IN (' + ','.join(['%s'] * len(VARIABLES)) + ')', VARIABLES)
            report['variables'] = {r['VARIABLE_NAME']: r['VARIABLE_VALUE'] for r in cursor.fetchall()}
            cursor.execute("SELECT User,Host,plugin,account_locked,password_expired FROM mysql.user WHERE User LIKE 'mysql_innodb_cluster_%'")
            report['accounts'] = cursor.fetchall()
            cursor.execute('SELECT * FROM performance_schema.host_cache')
            report['host_cache'] = cursor.fetchall()
            cursor.execute("SELECT User_name,User_password FROM mysql.slave_master_info WHERE Channel_name='group_replication_recovery'")
            credential = cursor.fetchone()
            report['recovery_user'] = credential['User_name'] if credential else None
            report['recovery_password_present'] = bool(credential and credential['User_password'])
            if credential:
                cursor.execute('SHOW GRANTS FOR %s@%s', (credential['User_name'], '%'))
                report['recovery_grants'] = [next(iter(row.values())) for row in cursor.fetchall()]
            cursor.execute("SELECT LOGGED,PRIO,ERROR_CODE,DATA FROM performance_schema.error_log WHERE LOGGED >= '2026-10-22 11:44:00' AND LOGGED <= '2026-10-22 11:46:10' ORDER BY LOGGED")
            report['experiment_errors'] = cursor.fetchall()
        for peer in HOSTS:
            if peer == source:
                continue
            result = {'target': peer}
            try:
                result['dns_addresses'] = sorted({entry[4][0] for entry in socket.getaddrinfo(peer, 3306, type=socket.SOCK_STREAM)})
                started = time.monotonic()
                with socket.create_connection((peer, 3306), timeout=5) as tcp:
                    result['local_address'] = tcp.getsockname()[0]
                    result['remote_address'] = tcp.getpeername()[0]
                result['tcp_connect_ms'] = (time.monotonic() - started) * 1000
                with connect(admin=True, host=peer, port=3306) as remote:
                    with remote.cursor(dictionary=True) as cursor:
                        cursor.execute('SELECT @@hostname AS hostname, @@server_uuid AS uuid')
                        result['root_identity'] = cursor.fetchone()
                if credential and credential['User_password']:
                    # Never include the credential dict or the password in the returned report.
                    with mysql.connector.connect(host=peer, port=3306, user=credential['User_name'],
                            password=credential['User_password'], use_pure=True, connection_timeout=5) as recovery:
                        with recovery.cursor() as cursor:
                            cursor.execute('SELECT @@hostname, @@server_uuid')
                            identity = cursor.fetchone()
                            result['recovery_identity'] = {'hostname': identity[0], 'uuid': identity[1]}
                            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                            result['recovery_tls_cipher'] = cursor.fetchone()[1]
                    result['recovery_authentication'] = 'PASS'
                else:
                    result['recovery_authentication'] = 'NO_STORED_CREDENTIAL'
            except (mysql.connector.Error, OSError) as error:
                result['error_type'] = type(error).__name__
                result['errno'] = getattr(error, 'errno', None)
                # Error text is omitted so future driver changes cannot disclose credentials.
                result['recovery_authentication'] = 'FAIL_OR_INCOMPLETE'
            report['peers'].append(result)
    credential = None
    return report


def report_ok(report):
    if report['local']['hostname'] != report['source']:
        return False
    peers = report['peers']
    if len(peers) != 2 or {p['target'] for p in peers} != set(HOSTS) - {report['source']}:
        return False
    for peer in peers:
        root = peer.get('root_identity', {})
        recovery = peer.get('recovery_identity', {})
        if peer.get('recovery_authentication') != 'PASS' or not peer.get('recovery_tls_cipher'):
            return False
        if root.get('hostname') != peer['target'] or recovery != root or not root.get('uuid'):
            return False
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', choices=HOSTS)
    report = collect(parser.parse_args().source)
    report['current_peer_checks_passed'] = report_ok(report)
    print(json.dumps(report, default=str, indent=2))
    raise SystemExit(0 if report['current_peer_checks_passed'] else 1)
