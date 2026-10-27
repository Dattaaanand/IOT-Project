// MQTT broker for the Smart Water Distribution project (Aedes, pure Node.js).
//   node broker/broker.js            -> mqtt://localhost:1883
// Any standard broker works too (e.g. Mosquitto: see broker/mosquitto.conf).
const aedes = require('aedes')();
const net = require('net');

const PORT = parseInt(process.env.MQTT_PORT || '1883', 10);
const server = net.createServer(aedes.handle);

server.listen(PORT, () => console.log(`[broker] MQTT broker listening on port ${PORT}`));

aedes.on('client', (c) => console.log(`[broker] client connected: ${c.id}`));
aedes.on('clientDisconnect', (c) => console.log(`[broker] client disconnected: ${c.id}`));
aedes.on('subscribe', (subs, c) =>
  console.log(`[broker] ${c ? c.id : '?'} subscribed to ${subs.map((s) => s.topic).join(', ')}`));

// message counter, printed every 10 s
let count = 0;
aedes.on('publish', (packet, c) => { if (c) count++; });
setInterval(() => { if (count) console.log(`[broker] ${count} messages in last 10 s`); count = 0; }, 10000);

process.on('SIGINT', () => { server.close(); aedes.close(() => process.exit(0)); });
