// Never force quorum or recreate cluster metadata on a bootstrap failure.
const password = os.getenv('MYSQL_ROOT_PASSWORD');
const appPassword = os.getenv('LAB_APP_PASSWORD');
const hosts = ['mysql-1', 'mysql-2', 'mysql-3'];
function connect(host, port = 3306, user = 'root', secret = password) {
    return mysql.getSession({host, port, user, password: secret});
}
function waitFor(check, description) {
    const deadline = Date.now() + 180000;
    let last;
    while (Date.now() < deadline) {
        try { return check(); } catch (error) {
            if (String(last) !== String(error)) print(description + ': ' + error);
            last = error; os.sleep(2);
        }
    }
    throw new Error(description + ': ' + last);
}
function assertHealthy(session) {
    const rows = session.runSql('SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE FROM performance_schema.replication_group_members').fetchAll();
    let online = 0;
    let primaries = 0;
    for (let i = 0; i < rows.length; i++) {
        if (rows[i][1] === 'ONLINE') online++;
        if (rows[i][2] === 'PRIMARY') primaries++;
    }
    if (rows.length !== 3 || online !== 3 || primaries !== 1) {
        throw new Error('Expected 3 ONLINE members and exactly 1 PRIMARY: ' + JSON.stringify(rows));
    }
    return rows;
}

const mode = os.getenv('LAB_MODE') || 'bootstrap';
if (mode === 'bootstrap') {
    if (!/^Ver 8\.4\./.test(shell.version)) throw new Error('MySQL Shell 8.4 required: ' + shell.version);
    for (const host of hosts) {
        waitFor(() => { const s = connect(host); s.runSql('SELECT 1'); s.close(); return true; }, host + ' readiness');
        dba.configureInstance({host, port: 3306, user: 'root', password});
    }
    shell.connect({host: hosts[0], port: 3306, user: 'root', password});
    const metadataExists = session.runSql("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='mysql_innodb_cluster_metadata'").fetchOne()[0];
    const cluster = metadataExists ? dba.getCluster('rewardCluster') : dba.createCluster('rewardCluster', {gtidSetIsComplete: true, communicationStack: 'MYSQL'});
    for (const host of hosts.slice(1)) {
        const topology = cluster.status().defaultReplicaSet.topology;
        if (!Object.prototype.hasOwnProperty.call(topology, host + ':3306')) {
            cluster.addInstance({host, port: 3306, user: 'root', password}, {recoveryMethod: 'incremental'});
        }
    }
    waitFor(() => assertHealthy(session), 'cluster readiness');
    const primary = session.runSql("SELECT MEMBER_HOST FROM performance_schema.replication_group_members WHERE MEMBER_ROLE='PRIMARY' AND MEMBER_STATE='ONLINE'").fetchOne()[0];
    const writer = connect(primary);
    writer.runSql('CREATE DATABASE IF NOT EXISTS reward_lab');
    writer.runSql('CREATE TABLE IF NOT EXISTS reward_lab.setup_checks (check_id CHAR(36) PRIMARY KEY, created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)) ENGINE=InnoDB');
    writer.runSql("CREATE USER IF NOT EXISTS 'lab_app'@'%' IDENTIFIED BY ?", [appPassword]);
    writer.runSql("ALTER USER 'lab_app'@'%' IDENTIFIED BY ?", [appPassword]);
    writer.runSql("GRANT SELECT, INSERT ON reward_lab.setup_checks TO 'lab_app'@'%'");
    writer.close();
    print(JSON.stringify(cluster.status({extended: 1}), null, 2));
} else if (mode === 'status') {
    let inspected = false;
    for (const host of hosts) {
        try {
            const s = connect(host);
            print(JSON.stringify({host, members: s.runSql('SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE FROM performance_schema.replication_group_members').fetchAll(), gtid_executed: s.runSql('SELECT @@GLOBAL.gtid_executed').fetchOne()[0]}));
            s.close(); inspected = true;
        } catch (error) { print(JSON.stringify({host, error: String(error)})); }
    }
    if (!inspected) throw new Error('No reachable nodes');
} else if (mode === 'smoke') {
    waitFor(() => {
        const s = connect('router', 6446, 'lab_app', appPassword);
        try {
            const observer = connect('mysql-1');
            let primary;
            try {
                assertHealthy(observer);
                primary = observer.runSql("SELECT MEMBER_ID FROM performance_schema.replication_group_members WHERE MEMBER_ROLE='PRIMARY'").fetchOne()[0];
            } finally { observer.close(); }
            const role = s.runSql('SELECT @@server_uuid, @@read_only').fetchOne();
            if (role[0] !== primary || role[1] !== 0) throw new Error('Router did not select writable PRIMARY');
            const id = s.runSql('SELECT UUID()').fetchOne()[0];
            s.startTransaction();
            s.runSql('INSERT INTO reward_lab.setup_checks(check_id) VALUES (?)', [id]);
            s.commit();
            if (s.runSql('SELECT COUNT(*) FROM reward_lab.setup_checks WHERE check_id=?', [id]).fetchOne()[0] !== 1) throw new Error('Committed row missing');
            const gtid = s.runSql('SELECT @@GLOBAL.gtid_executed').fetchOne()[0];
            const verifiedNodes = [];
            for (const host of hosts) {
                const replica = connect(host);
                try {
                    if (replica.runSql('SELECT WAIT_FOR_EXECUTED_GTID_SET(?, 10)', [gtid]).fetchOne()[0] !== 0) throw new Error(host + ': GTID wait timed out');
                    if (replica.runSql('SELECT COUNT(*) FROM reward_lab.setup_checks WHERE check_id=?', [id]).fetchOne()[0] !== 1) throw new Error(host + ': committed row missing');
                    verifiedNodes.push(host);
                } finally { replica.close(); }
            }
            print(JSON.stringify({result: 'PASS', check_id: id, primary_uuid: primary, verified_nodes: verifiedNodes}) + '\n');
            return true;
        } finally { s.close(); }
    }, 'Router write check');
} else { throw new Error('Unknown LAB_MODE: ' + mode); }
