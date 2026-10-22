import unittest

from workload.network_diagnostics import report_ok


class DiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        self.report = {'source': 'mysql-1', 'local': {'hostname': 'mysql-1'}, 'peers': []}
        for host in ('mysql-2', 'mysql-3'):
            identity = {'hostname': host, 'uuid': 'uuid-' + host}
            self.report['peers'].append({'target': host, 'root_identity': identity.copy(),
                'recovery_identity': identity.copy(), 'recovery_authentication': 'PASS',
                'recovery_tls_cipher': 'TLS_AES_256_GCM_SHA384'})

    def test_complete_peer_matrix_is_accepted(self):
        self.assertTrue(report_ok(self.report))

    def test_wrong_host_is_not_accepted_as_tcp_success(self):
        self.report['peers'][0]['root_identity']['hostname'] = 'mysql-3'
        self.assertFalse(report_ok(self.report))

    def test_missing_tls_is_not_a_complete_authentication_check(self):
        self.report['peers'][0]['recovery_tls_cipher'] = ''
        self.assertFalse(report_ok(self.report))

    def test_missing_peer_is_not_accepted(self):
        self.report['peers'].pop()
        self.assertFalse(report_ok(self.report))
