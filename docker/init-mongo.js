const appDb = db.getSiblingDB(process.env.MONGO_INITDB_DATABASE);
const user = process.env.MONGO_APP_USER;
const password = require('fs').readFileSync('/run/secrets/mongo_password', 'utf8').replace(/\n$/, '');
const adminPassword = require('fs').readFileSync('/run/secrets/mongo_admin_password', 'utf8').replace(/\n$/, '');
if (!/^[a-z_][a-z0-9_]{0,62}$/.test(user) || !password.trim() || /[\r\n\0]/.test(password) || password === adminPassword) {
    throw new Error('Invalid application credentials.');
}
if (process.env.HW03_ROTATE_APP_PASSWORD === '1') {
    db.getSiblingDB('admin').auth(process.env.MONGO_INITDB_ROOT_USERNAME, adminPassword);
    appDb.updateUser(user, {pwd: password});
} else {
    appDb.createUser({user, pwd: password, roles: [{role: 'readWrite', db: appDb.getName()}]});
}
