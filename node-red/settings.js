/**
 * Node-RED settings for the Smart Water Distribution project.
 *   node-red --userDir node-red          (or: npm start inside this folder)
 *   editor:    http://localhost:1880
 *   dashboard: http://localhost:1880/ui
 *
 * The SQLite database is opened here with Node.js' built-in `node:sqlite`
 * module (no native add-on to compile) and shared with function nodes as
 * global.get('db').
 */
const path = require('path');
const fs = require('fs');
const { DatabaseSync } = require('node:sqlite');

const ROOT = path.resolve(__dirname, '..');
const config = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
const DB_PATH = path.resolve(ROOT, config.db_path);

fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
const db = new DatabaseSync(DB_PATH);
db.exec('PRAGMA busy_timeout = 5000');
db.exec(fs.readFileSync(path.join(ROOT, 'db', 'schema.sql'), 'utf8'));
console.log(`[wds] SQLite database: ${DB_PATH}`);

module.exports = {
    uiPort: process.env.PORT || 1880,
    flowFile: 'flows.json',
    flowFilePretty: true,
    credentialSecret: false,

    // serve ML result images (confusion matrices, comparison chart) at /results/
    httpStatic: [{ path: path.join(ROOT, 'ml', 'results'), root: '/results/' }],

    functionGlobalContext: {
        db,                     // node:sqlite DatabaseSync
        wdsRoot: ROOT,
        wdsConfig: config,
    },
    exportGlobalContextKeys: false,
    functionExternalModules: true,

    ui: { path: 'ui' },

    logging: { console: { level: 'info', metrics: false, audit: false } },
    editorTheme: {
        projects: { enabled: false },
        tours: false,
        page: { title: 'Smart Water Distribution - Node-RED' },
        header: { title: 'Smart Water Distribution & Leakage Detection' },
    },
};
