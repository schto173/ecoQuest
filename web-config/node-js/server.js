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
const MQTT_PASSWORD = "marathon"; // Consider environment variables for production

// --- MQTT Topics ---
const MQTT_TOPIC_CONFIG_START = "config/start_line";
const MQTT_TOPIC_CONFIG_FINISH = "config/finish_line";
const MQTT_TOPIC_CONFIG_LAP = "config/lap_line";
const MQTT_TOPIC_CONFIG_TOTAL_LAPS = "config/total_laps";
const MQTT_TOPIC_IDEAL_TIME = "config/ideal_time"; // Correct topic

const configTopics = [
    MQTT_TOPIC_CONFIG_START,
    MQTT_TOPIC_CONFIG_FINISH,
    MQTT_TOPIC_CONFIG_LAP,
    MQTT_TOPIC_CONFIG_TOTAL_LAPS,
    MQTT_TOPIC_IDEAL_TIME // Included here
];

// --- In-memory storage for current config ---
// Store the latest *payload string* received for each topic
const currentConfig = {
    [MQTT_TOPIC_CONFIG_START]: null,
    [MQTT_TOPIC_CONFIG_FINISH]: null,
    [MQTT_TOPIC_CONFIG_LAP]: null,
    [MQTT_TOPIC_CONFIG_TOTAL_LAPS]: null,
    [MQTT_TOPIC_IDEAL_TIME]: null, // Included here
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
    // Subscribe to all config topics
    mqttClient.subscribe(configTopics, { qos: 2 }, (err, granted) => {
        if (err) {
            console.error('Failed to subscribe to config topics:', err);
        } else {
            console.log('Subscribed to config topics:', granted.map(g => g.topic).join(', '));
            // Retained messages should be sent automatically by the broker upon subscription
        }
    });
});

// --- Handle incoming messages (including retained ones) ---
mqttClient.on('message', (topic, message) => {
    // message is a Buffer, convert to string
    const payload = message.toString();
    console.log(`Received message on topic ${topic}`); // Log topic, avoid logging payload by default for security/privacy

    // Update our in-memory store if it's a config topic we care about
    if (configTopics.includes(topic)) {
        console.log(`Updating stored config for ${topic}`);
        currentConfig[topic] = payload; // Store the raw string payload
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
    const responseConfig = {};
    let parsingError = false; // Track if any non-JSON parsing fails

    try {
        // Parse Start Line (JSON)
        if (currentConfig[MQTT_TOPIC_CONFIG_START]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_START]);
            // Convert stored [lon, lat] back to Leaflet [lat, lon]
            responseConfig.start = [[data.p1[1], data.p1[0]], [data.p2[1], data.p2[0]]];
        } else { responseConfig.start = null; }

        // Parse Finish Line (JSON)
        if (currentConfig[MQTT_TOPIC_CONFIG_FINISH]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_FINISH]);
            responseConfig.finish = [[data.p1[1], data.p1[0]], [data.p2[1], data.p2[0]]];
        } else { responseConfig.finish = null; }

        // Parse Lap Line (JSON)
        if (currentConfig[MQTT_TOPIC_CONFIG_LAP]) {
            const data = JSON.parse(currentConfig[MQTT_TOPIC_CONFIG_LAP]);
            responseConfig.lap = [[data.p1[1], data.p1[0]], [data.p2[1], data.p2[0]]];
        } else { responseConfig.lap = null; }

    } catch (e) {
        // If JSON parsing fails, log error and stop processing this request
        console.error("Error parsing stored line config JSON:", e);
        return res.status(500).send({ message: "Error parsing stored line configuration from MQTT." });
    }

    // Parse Total Laps (Number) - Do this outside the JSON try-catch
    if (currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS]) {
        responseConfig.totalLaps = parseInt(currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS], 10);
        if (isNaN(responseConfig.totalLaps)) {
             console.warn(`Stored total laps value is not a valid integer: "${currentConfig[MQTT_TOPIC_CONFIG_TOTAL_LAPS]}"`);
             responseConfig.totalLaps = 0; // Default to 0 on parse error
             parsingError = true;
        }
    } else {
        responseConfig.totalLaps = 0; // Default if not set
    }

    // Parse Ideal Time (Number) - Do this outside the JSON try-catch
    if (currentConfig[MQTT_TOPIC_IDEAL_TIME]) {
        responseConfig.idealTime = parseFloat(currentConfig[MQTT_TOPIC_IDEAL_TIME]);
        if (isNaN(responseConfig.idealTime)) {
             console.warn(`Stored ideal time value is not a valid number: "${currentConfig[MQTT_TOPIC_IDEAL_TIME]}"`);
             responseConfig.idealTime = 60; // Default to 60 on parse error
             parsingError = true;
        }
    } else {
        responseConfig.idealTime = 60; // Default to 60 if not set
    }

    // Log if any non-JSON parsing failed but continue
    if (parsingError) {
        console.warn("One or more numeric config values failed to parse and were defaulted.");
    }

    console.log("Sending current config to client:", responseConfig);
    res.status(200).send(responseConfig);
});


// POST Endpoint to set new configuration
app.post('/api/set-lines', (req, res) => {
    console.log('Received /api/set-lines request');
    // --- Log received body ---
    console.log('Request Body Received:', JSON.stringify(req.body, null, 2));

    // --- Destructure expected fields ---
    const { start, finish, lap, totalLaps, idealTime } = req.body;

    // --- Log destructured values ---
    console.log(`Destructured values - totalLaps: ${totalLaps}, idealTime: ${idealTime}`);

    // --- Validation ---
    if (!start || !finish || !lap) {
        return res.status(400).send({ message: 'Missing line data (start, finish, or lap).' });
    }
    if (!Array.isArray(start) || start.length !== 2 || !Array.isArray(finish) || finish.length !== 2 || !Array.isArray(lap) || lap.length !== 2) {
        return res.status(400).send({ message: 'Incorrect line format. Each line must be an array of two [lat, lon] points.' });
    }
     // Validate points within lines
    const isValidPoint = (p) => Array.isArray(p) && p.length === 2 && typeof p[0] === 'number' && typeof p[1] === 'number';
    if (!isValidPoint(start[0]) || !isValidPoint(start[1]) || !isValidPoint(finish[0]) || !isValidPoint(finish[1]) || !isValidPoint(lap[0]) || !isValidPoint(lap[1])) {
        return res.status(400).send({ message: 'Incorrect point format within a line. Points must be [lat, lon] arrays.' });
    }
    // Validate totalLaps
    if (totalLaps === undefined || totalLaps === null || typeof totalLaps !== 'number' || totalLaps < 0 || !Number.isInteger(totalLaps)) {
         return res.status(400).send({ message: 'Invalid total laps value. Must be a whole number >= 0.' });
    }
    // Validate idealTime
    if (idealTime === undefined || idealTime === null || typeof idealTime !== 'number' || idealTime < 0) {
         return res.status(400).send({ message: 'Invalid ideal time value. Must be a number >= 0.' });
    }
    // Check MQTT connection
    if (!mqttClient.connected) {
        console.error("MQTT client not connected during /api/set-lines request.");
        return res.status(500).send({ message: 'MQTT client not connected. Cannot save configuration.' });
    }

    // --- Prepare Publish Tasks ---
    const linesToPublish = [
        { topic: MQTT_TOPIC_CONFIG_START, line: start, name: 'Start' },
        { topic: MQTT_TOPIC_CONFIG_FINISH, line: finish, name: 'Finish' },
        { topic: MQTT_TOPIC_CONFIG_LAP, line: lap, name: 'Lap' },
    ];
    const configToPublish = [
         { topic: MQTT_TOPIC_CONFIG_TOTAL_LAPS, value: totalLaps, name: 'Total Laps'},
         { topic: MQTT_TOPIC_IDEAL_TIME, value: idealTime, name: 'Ideal Time'} // Correctly included
    ];
    const publishOptions = { qos: 2, retain: true }; // Use QoS 2 and retain messages
    let errors = [];
    let successes = 0;
    const totalTasks = linesToPublish.length + configToPublish.length;
    let responseSent = false;

    // --- Callback to check completion ---
    function checkCompletion() {
        if (responseSent) return; // Prevent sending multiple responses
        if (successes + errors.length === totalTasks) {
            responseSent = true;
            if (errors.length > 0) {
                console.error(`Finished publishing with ${errors.length} error(s):`, errors);
                res.status(500).send({ message: 'Error publishing some configuration parts.', details: errors });
            } else {
                console.log('All lines and config published successfully!');
                res.status(200).send({ message: 'All lines and config published successfully!' });
            }
        }
    }

    // --- Publish Lines ---
    linesToPublish.forEach(({ topic, line, name }) => {
        // Convert Leaflet [lat, lon] back to expected [lon, lat] for payload
        const payload = JSON.stringify({ p1: [line[0][1], line[0][0]], p2: [line[1][1], line[1][0]] });
        // Log before publishing
        console.log(`--> Publishing Line [${name}] to TOPIC: "${topic}" PAYLOAD: ${payload}`);
        mqttClient.publish(topic, payload, publishOptions, (err) => {
            if (err) {
                console.error(`Failed to publish ${name} line to ${topic}:`, err);
                errors.push(`Failed ${name} line.`);
            } else {
                console.log(`Published ${name} line successfully to ${topic}.`);
                successes++;
            }
            checkCompletion(); // Check if all publishes are done
        });
    });

    // --- Publish Config Values ---
    configToPublish.forEach(({ topic, value, name }) => {
         const payload = String(value); // Convert number to string for MQTT payload
         // Log before publishing
         console.log(`--> Publishing Config [${name}] to TOPIC: "${topic}" with VALUE: "${payload}"`);
         mqttClient.publish(topic, payload, publishOptions, (err) => {
            if (err) {
                console.error(`Failed to publish ${name} config to ${topic}:`, err);
                errors.push(`Failed ${name} config.`);
            } else {
                console.log(`Published ${name} config successfully to ${topic}.`);
                successes++;
            }
            checkCompletion(); // Check if all publishes are done
        });
    });

    // --- Timeout for MQTT confirmations ---
    setTimeout(() => {
        if (!responseSent) {
            console.error("Timeout waiting for MQTT publish confirmations.");
            responseSent = true; // Prevent checkCompletion from sending another response
            // Send response indicating timeout, include any errors received so far
            res.status(508).send({ // 508 Loop Detected might be slightly more appropriate than 500/504
                 message: 'Timeout waiting for all MQTT publish confirmations. Some settings might not have saved.',
                 details: errors
            });
        }
    }, 10000); // 10 second timeout
});

// --- Serve the main HTML file ---
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// --- Start Server ---
app.listen(port, () => {
    console.log(`Web server listening at http://localhost:${port}`);
});

// --- Graceful Shutdown ---
process.on('SIGINT', () => {
    console.log('\nSIGINT received. Closing MQTT connection and shutting down server...');
    // Attempt to gracefully close MQTT connection
    mqttClient.end(true, () => { // Pass true to force close even if offline
        console.log('MQTT client closed.');
        process.exit(0); // Exit cleanly
    });
    // Set a timeout to force exit if MQTT doesn't close promptly
    setTimeout(() => {
        console.error('MQTT client did not close gracefully after 3 seconds. Forcing exit.');
        process.exit(1); // Exit with error code
    }, 3000);
});