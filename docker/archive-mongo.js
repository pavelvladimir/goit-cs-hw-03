// mongosh supplies authentication and the empty-target check; tools stream the archive.
const fs = require('fs');
const { spawnSync } = require('child_process');
const mode = process.env.HW03_ARCHIVE_MODE;
if (!['backup', 'restore', 'info'].includes(mode)) {
    console.error('Set HW03_ARCHIVE_MODE=backup|restore|info.');
    quit(2);
}
const database = process.env.MONGO_INITDB_DATABASE;
const username = process.env.MONGO_APP_USER;
const password = fs.readFileSync('/run/secrets/mongo_password', 'utf8').replace(/\n$/, '');
const appDb = db.getSiblingDB(database);
appDb.auth(username, password);
if (mode === 'info') {
    print(JSON.stringify({ database, server_version: appDb.version() }));
    quit(0);
}
if (mode === 'restore' && appDb.getCollectionNames().length !== 0) {
    console.error('Restore refused: target database is not empty.');
    quit(1);
}
// JSON is valid YAML. Keep credentials out of argv and remove the private file afterward.
const directory = process.env.HW03_ARCHIVE_DIRECTORY || fs.mkdtempSync('/tmp/hw03-archive-');
fs.chmodSync(directory, 0o700);
const config = directory + '/credentials.yaml';
let status = 1;
try {
    fs.writeFileSync(config, JSON.stringify({ password }), { mode: 0o600 });
    const args = [
        '--host=127.0.0.1', '--username=' + username,
        '--authenticationDatabase=' + database, '--config=' + config,
        '--archive', '--gzip',
    ];
    if (mode === 'backup') {
        args.push('--db=' + database);
    } else {
        args.push('--nsInclude=' + database + '.*', '--stopOnError');
    }
    const result = spawnSync(mode === 'backup' ? 'mongodump' : 'mongorestore', args, {
        stdio: 'inherit',
    });
    status = result.status ?? 1;
} finally {
    fs.rmSync(config, { force: true });
    if (!process.env.HW03_ARCHIVE_DIRECTORY) {
        fs.rmdirSync(directory);
    }
}
quit(status);
