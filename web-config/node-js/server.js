// server.js
const express = require('express');
const mqtt = require('mqtt');
const path = require('path');

const app = express();
const port = 3000; // Or your preferred port

// --- MQTT Configuration ---
const MQTT_BROKER = "mqtt://tome.lu"; // Use mqtt:// prefix
const MQTT_PORT = 1883; // Default MQTT port
const MQTT_USERNAME = "eco";
const MQTT_PASSWORD = "marathon"; // Consider environment variables

// --- MQTT Topics ---
const MQTT_TOPIC_CONFIG_START = "gps/config/start_line";
const MQTT_TOPIC_CONFIG_FINISH = "gps/config/finish_line";
const MQTT_TOPIC_CONFIG_LAP = "gps/config/lap_line";
const MQTT_TOPIC_CONFIG_TOTAL_LAPS = "gps/config/total_laps";

const configTopics = [
    MQTT_TOPIC_CONFIG_START,
    MQTT_TOPIC_CONFIG_FINISH,
    MQTT_TOPIC_CONFIG_LAP,
    MQTT_TOPIC_CONFIG_TOTAL_LAPS
];

// --- In-memory storage for current config ---
// Store the latest *payload string* received for each topic
const currentConfig = {
    [MQTT_TOPIC_CONFIG_START]: null,
    [MQTT_TOPIC_CONFIG_FINISH]: null,
    [MQTT_TOPIC_CONFIG_LAP]: null,
    [MQTT_TOPIC_CONFIG_TOTAL_LAPS]: null,
};

// --- MQTT Client Setup ---
const mqttOptions = {
    port: MQTT_PORT,
    username: MQTT_USERNAME,
    password: MQTT_PASSWORD,
    clientId: `gps_config_webapp_${Math.random().toString(16).substr(2, 8)}`, // Unique client ID
    connectTimeout: 5000, // 5 seconds
    reconnectPeriod: 1000, // Try reconnecting every second if disconnected
};

console.log(`Attempting to connect to MQTT broker at ${MQTT_BROKER}`);
const mqttClient = mqtt.connect(MQTT_BROKER, mqttOptions);

mqttClient.on('connect', () => {
    console.log('Successfully connected to MQTT broker');
    // Subscribe to config topics to get retained messages and updates
    mqttClient.subscribe(configTopics, { qos: 2 }, (err, granted) => {
        if (err) {
            console.error('Failed to subscribe to config topics:', err);
        } else {
            console.log('Subscribed to config topics:', granted.map(g => g.topic).join(', '));
            // Request retained messages explicitly? Not usually needed, broker should send on subscribe.
        }
    });
});

// --- Handle incoming messages (including retained ones) ---
mqttClient.on('message', (topic, message) => {
    // message is a Buffer, convert to string
    const payload = message.toString();
    console.log(`Received message on topic ${topic}`); //: ${payload}`); // Don't log payload by default

    // Update our in-memory store if it's a config topic
    if (configTopics.includes(topic)) {
        console.log(`Updating stored config for ${topic}`);
        currentConfig[topic] = payload;
    } else {
        console.log(`Ignoring message on non-config topic: ${topic}`);
    }
});


mqttClient.on('error', (err) => {
    console.error('MQTT Connection Error:', err);
});

mqttClient.on('reconnect', () => {
    console.log('Reconnecting to MQTT broker...');
});

mqttClient.on('close', () => {
    console.log('MQTT connection closed.');
});

mqttClient.on('offline', () => {
    console.log('MQTT client is offline.');
});

// --- Express Middleware ---
app.use(express.json()); // For parsing application/json
app.use(express.static(path.join(__dirname, 'public'))); // Serve static files (HTML, CSS, JS)

// --- API Endpoints ---

// GET Endpoint to retrieve current configuration
app.get('/api/get-config', (req, res) => {
    console.log('Received /api/get-config request');
    // Prepare the response object by parsing the stored payloads
    const responseConfig = {};
    let parsingError = false;

    try {
        // Parse Start Line
        if (currentConfig[MQTT_TOPIC_CONFIG_START]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_START]);
            // Convert back to Leaflet format [lat, lon]
            responseConfig.start = [
                [data.p1[1], data.p1[0]], // lat, lon
                [data.p2[1], data.p2[0]]  // lat, lon
            ];
        } else {
            responseConfig.start = null;
        }

        // Parse Finish Line
        if (currentConfig[MQTT_TOPIC_CONFIG_FINISH]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_FINISH]);
            responseConfig.finish = [
                [data.p1[1], data.p1[0]],
                [data.p2[1], data.p2[0]]
            ];
        } else {
            responseConfig.finish = null;
        }

        // Parse Lap Line
        if (currentConfig[MQTT_TOPIC_CONFIG_LAP]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_LAP]);
            responseConfig.lap = [
                [data.p1[1], data.p1[0]],
                [data.p2[1], data.p2[0]]
            ];
        } else {
            responseConfig.lap = null;
        }

        // Parse Total Laps
        if (currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS]) {
            responseConfig.totalLaps = parseInt(currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS], 10);
            if (isNaN(responseConfig.totalLaps)) {
                 console.warn(`Stored total laps value is not a number: ${currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS]}`);
                 responseConfig.totalLaps = 0; // Default to 0 on parse error
                 parsingError = true;
            }
        } else {
            responseConfig.totalLaps = 0; // Default if not set
        }

    } catch (e) {
        console.error("Error parsing stored config JSON:", e);
        // Don't send partially parsed data if there was an error
        return res.status(500).send({ message: "Error parsing stored configuration from MQTT." });
    }

    console.log("Sending current config:", responseConfig);
    res.status(200).send(responseConfig);
});


// POST Endpoint to set new configuration
app.post('/api/set-lines', (req, res) => {
    console.log('Received /api/set-lines request');
    const { start, finish, lap, totalLaps } = req.body;

    // --- Validation (as before) ---
    if (!start || !finish || !lap) return res.status(400).send({ message: 'Missing line data.' });
    if (!Array.isArray(start) || start.length !== 2 || !Array.isArray(finish) || finish.length !== 2 || !Array.isArray(lap) || lap.length !== 2) {
        return res.status(400).send({ message: 'Incorrect line format.' });
    }
    if (totalLaps === undefined || totalLaps === null || typeof totalLaps !== 'number' || totalLaps < 0 || !Number.isInteger(totalLaps)) {
         return res.status(400).send({ message: 'Invalid total laps value.' });
    }
    if (!mqttClient.connected) return res.status(500).send({ message: 'MQTT client not connected.' });

    // --- Prepare Publish Tasks (as before) ---
    const linesToPublish = [
        { topic: MQTT_TOPIC_CONFIG_START, line: start, name: 'Start' },
        { topic: MQTT_TOPIC_CONFIG_FINISH, line: finish, name: 'Finish' },
        { topic: MQTT_TOPIC_CONFIG_LAP, line: lap, name: 'Lap' },
    ];
    const configToPublish = [
         { topic: MQTT_TOPIC_CONFIG_TOTAL_LAPS, value: totalLaps, name: 'Total Laps'}
    ];
    const publishOptions = { qos: 2, retain: true };
    let errors = [];
    let successes = 0;
    const totalTasks = linesToPublish.length + configToPublish.length;
    let responseSent = false;

    function checkCompletion() { // (as before)
        if (responseSent) return;
        if (successes + errors.length === totalTasks) {
            responseSent = true;
            if (errors.length > 0) res.status(500).send({ message: 'Error publishing some lines/config.', details: errors });
            else res.status(200).send({ message: 'All lines and config published successfully!' });
        }
    }

    // --- Publish Lines (as before) ---
    linesToPublish.forEach(({ topic, line, name }) => {
        const payload = JSON.stringify({ p1: [line[0][1], line[0][0]], p2: [line[1][1], line[1][0]] }); // lon, lat
        mqttClient.publish(topic, payload, publishOptions, (err) => {
            if (err) { console.error(`Failed to publish ${name} line:`, err); errors.push(`Failed ${name}.`); }
            else { console.log(`Published ${name} line.`); successes++; }
            checkCompletion();
        });
    });

    // --- Publish Config (as before) ---
    configToPublish.forEach(({ topic, value, name }) => {
         const payload = String(value);
         mqttClient.publish(topic, payload, publishOptions, (err) => {
            if (err) { console.error(`Failed to publish ${name} config:`, err); errors.push(`Failed ${name}.`); }
            else { console.log(`Published ${name} config.`); successes++; }
            checkCompletion();
        });
    });

    // Timeout (as before)
    setTimeout(() => {
        if (!responseSent) {
            console.error("Timeout waiting for MQTT publish confirmations.");
            responseSent = true;
            res.status(500).send({ message: 'Timeout waiting for MQTT publish confirmations.', details: errors });
        }
    }, 10000);
});

// --- Serve the main HTML file ---
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// --- Start Server ---
app.listen(port, () => {
    console.log(`Web server listening at http://localhost:${port}`);
});

// --- Graceful Shutdown (as before) ---
process.on('SIGINT', () => {
    console.log('\nSIGINT received. Closing MQTT connection and shutting down server.');
    mqttClient.end(true, () => {
        console.log('MQTT client closed.');
        process.exit(0);
    });
    setTimeout(() => { console.log('Forcing exit after timeout.'); process.exit(1); }, 3000);
});