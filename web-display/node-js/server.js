const express = require('express');
const http = require('http');
const { Server } = require("socket.io");
const mqtt = require('mqtt');
const path = require('path');

// --- Configuration ---
const MQTT_BROKER = "mqtt://tome.lu"; // Use mqtt:// prefix
const MQTT_PORT = 1883; // MQTT default port
const MQTT_USER = "eco";
const MQTT_PASSWORD = "marathon";
const MQTT_TOPIC_GPS = "gps/status";
const MQTT_TOPIC_SPEED = "car/speed"; // Listen for the speed published by the Pi
const WEB_PORT = 3001; // Port for the web interface

// --- Global State ---
let mqttConnected = false;
let raceData = {
    current_lap: 0,
    total_laps: 0,
    last_lap_time_seconds: null,
    race_finished: false,
    current_lap_elapsed_seconds: null
};
let speedData = {
    speed_kmh: 0.0
};

// --- Express and Socket.IO Setup ---
const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, 'public'))); // Serve static files

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public/index.html'));
});

// --- MQTT Client Setup ---
const mqttOptions = {
    port: MQTT_PORT,
    username: MQTT_USER,
    password: MQTT_PASSWORD,
    clientId: `web_monitor_${Math.random().toString(16).substr(2, 8)}`, // Unique client ID
    reconnectPeriod: 5000, // Try reconnecting every 5 seconds
    connectTimeout: 10 * 1000, // 10 seconds
};

console.log(`Attempting to connect to MQTT broker: ${MQTT_BROKER}`);
const client = mqtt.connect(MQTT_BROKER, mqttOptions);

client.on('connect', () => {
    console.log('MQTT: Connected successfully.');
    mqttConnected = true;
    io.emit('mqtt_status', { connected: true }); // Notify web clients

    client.subscribe(MQTT_TOPIC_GPS, { qos: 1 }, (err) => {
        if (!err) {
            console.log(`MQTT: Subscribed to ${MQTT_TOPIC_GPS}`);
        } else {
            console.error(`MQTT: Subscription error for ${MQTT_TOPIC_GPS}:`, err);
        }
    });
    // Also subscribe to the speed topic published BY the Pi
    client.subscribe(MQTT_TOPIC_SPEED, { qos: 0 }, (err) => {
         if (!err) {
            console.log(`MQTT: Subscribed to ${MQTT_TOPIC_SPEED}`);
        } else {
            console.error(`MQTT: Subscription error for ${MQTT_TOPIC_SPEED}:`, err);
        }
    });
});

client.on('reconnect', () => {
    console.log('MQTT: Reconnecting...');
    mqttConnected = false; // Assume disconnected during reconnect attempt
    io.emit('mqtt_status', { connected: false });
});

client.on('close', () => {
    console.log('MQTT: Connection closed.');
    mqttConnected = false;
    io.emit('mqtt_status', { connected: false });
});

client.on('offline', () => {
    console.log('MQTT: Client offline.');
    mqttConnected = false;
    io.emit('mqtt_status', { connected: false });
});

client.on('error', (err) => {
    console.error('MQTT: Connection error:', err);
    mqttConnected = false;
    io.emit('mqtt_status', { connected: false });
    // client.end(); // Consider if you want to force close on some errors
});

client.on('message', (topic, message) => {
    // console.log(`MQTT: Received message on ${topic}`); // Debug
    try {
        const payload = JSON.parse(message.toString());

        if (topic === MQTT_TOPIC_GPS) {
            // Basic validation
            const requiredKeys = ["current_lap", "total_laps", "last_lap_time_seconds", "race_finished", "current_lap_elapsed_seconds"];
            if (requiredKeys.every(key => key in payload)) {
                raceData = { ...raceData, ...payload }; // Update race data
                io.emit('race_update', raceData); // Send full update to web clients
            } else {
                console.warn(`MQTT: Incomplete GPS payload on ${topic}:`, payload);
            }
        } else if (topic === MQTT_TOPIC_SPEED) {
             if ('speed_kmh' in payload) {
                 speedData = { ...speedData, speed_kmh: payload.speed_kmh };
                 io.emit('speed_update', speedData); // Send speed update to web clients
             } else {
                 console.warn(`MQTT: Incomplete Speed payload on ${topic}:`, payload);
             }
        }
    } catch (e) {
        console.error(`MQTT: Error processing message on ${topic}:`, e);
    }
});

// --- Socket.IO Connection Handling ---
io.on('connection', (socket) => {
    console.log('Web client connected:', socket.id);

    // Send current state immediately to newly connected client
    socket.emit('initial_state', { raceData, speedData, mqttConnected });

    socket.on('disconnect', () => {
        console.log('Web client disconnected:', socket.id);
    });
});

// --- Start Server ---
server.listen(WEB_PORT, () => {
    console.log(`Web server listening on http://localhost:${WEB_PORT}`);
});

// Graceful shutdown
process.on('SIGINT', () => {
    console.log("Shutting down...");
    client.end();
    server.close(() => {
        console.log("Server closed.");
        process.exit(0);
    });
});