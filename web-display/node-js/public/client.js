const socket = io();

// --- Canvas Setup ---
const canvas = document.getElementById('tachoCanvas');
const ctx = canvas.getContext('2d');
const width = canvas.width;   // Now 512
const height = canvas.height; // Now 256

// --- Scale Factor ---
const scaleFactor = 4; // Factor to scale drawing elements

// --- DOM Element References ---
const lapInfoEl = document.getElementById('lapInfo');
const currentTimeEl = document.getElementById('currentTime');
const lastTimeEl = document.getElementById('lastTime');
const mqttStatusEl = document.getElementById('mqttStatus');
const digitalSpeedEl = document.getElementById('digitalSpeed');

// --- Drawing Constants (Scaled) ---
const baseCenterX = 132;
const baseCenterY = 68;
const baseInnerRadius = 48;
const baseOuterRadius = 58;
const baseEndYOffset = 15;
const baseTickLength = 4;
const baseLabelOffset = 8;

const centerX = baseCenterX * scaleFactor;
const centerY = baseCenterY * scaleFactor;
const innerRadius = baseInnerRadius * scaleFactor;
const outerRadius = baseOuterRadius * scaleFactor;
const endYOffset = baseEndYOffset * scaleFactor;
const tickLength = baseTickLength * scaleFactor;
const labelOffset = baseLabelOffset * scaleFactor;

// Unscaled constants
const startAngleDeg = 180;
const endAngleDeg = 90;
const maxSpeed = 50;

// --- State Variables ---
let currentSpeedKmh = 0;
let currentRaceData = {};
let isMqttConnected = false;

// --- Helper Functions ---
function degToRad(degrees) {
    return degrees * (Math.PI / 180);
}

function formatTimeJS(seconds) {
    // (Function remains the same)
    if (seconds === null || seconds === undefined) return "00:00";
    try {
        seconds = parseFloat(seconds);
        if (isNaN(seconds) || seconds < 0) return "00:00";
        let minutes = Math.floor(seconds / 60);
        let remainingSeconds = Math.round(seconds % 60);
        if (remainingSeconds === 60) {
            minutes += 1;
            remainingSeconds = 0;
        }
        return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
    } catch (e) {
        return "00:00";
    }
}

function pointOnArcJS(radius, angleDeg) {
    const angleRad = degToRad(angleDeg);
    let x = centerX + radius * Math.cos(angleRad);
    let y = centerY - radius * Math.sin(angleRad);

    if (Math.abs(angleDeg - endAngleDeg) < 1e-6) {
        y += endYOffset;
    }
    // No rounding here, let canvas handle subpixels for smoother lines
    return { x: x, y: y };
}

// --- Drawing Functions (Updated for Scale) ---
function drawArcOutlineJS() {
    ctx.fillStyle = 'white';
    // Draw thicker lines by drawing multiple points or using lines
    ctx.lineWidth = 1 * scaleFactor; // Make outline thicker
    ctx.strokeStyle = 'white';

    ctx.beginPath();
    // Draw outer arc
    ctx.arc(centerX, centerY, outerRadius, degToRad(startAngleDeg), degToRad(endAngleDeg), true); // Counter-clockwise
    ctx.stroke();

    ctx.beginPath();
    // Draw inner arc
    ctx.arc(centerX, centerY, innerRadius, degToRad(startAngleDeg), degToRad(endAngleDeg), true);
    ctx.stroke();

    // Alternative: Draw points (less smooth)
    // for (let angleDeg = endAngleDeg; angleDeg <= startAngleDeg; angleDeg += 0.5) { // Increase steps for density
    //     const pInner = pointOnArcJS(innerRadius, angleDeg);
    //     const pOuter = pointOnArcJS(outerRadius, angleDeg);
    //     ctx.fillRect(Math.round(pInner.x), Math.round(pInner.y), scaleFactor, scaleFactor); // Draw larger points
    //     ctx.fillRect(Math.round(pOuter.x), Math.round(pOuter.y), scaleFactor, scaleFactor);
    // }
}

function drawSpeedTicksJS() {
    ctx.strokeStyle = 'white';
    ctx.fillStyle = 'white';
    ctx.lineWidth = 1 * scaleFactor; // Scale line width
    // Scale font size (corresponds to Python's 8px tick_font)
    ctx.font = `bold ${8 * scaleFactor}px 'DejaVu Sans', sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    if (maxSpeed <= 0) return;

    for (let tick = 5; tick <= maxSpeed; tick += 5) {
        const angle = startAngleDeg - ((startAngleDeg - endAngleDeg) * (tick / maxSpeed));
        const outerPt = pointOnArcJS(outerRadius, angle);
        const innerPt = pointOnArcJS(outerRadius - tickLength, angle); // Use scaled tickLength

        // Draw tick line
        ctx.beginPath();
        ctx.moveTo(innerPt.x, innerPt.y); // No +0.5 needed for thicker lines
        ctx.lineTo(outerPt.x, outerPt.y);
        ctx.stroke();

        // Draw labels (only for multiples of 10)
        if (tick % 10 === 0) {
            const labelPt = pointOnArcJS(outerRadius + labelOffset, angle); // Use scaled labelOffset
            ctx.fillText(String(tick), labelPt.x, labelPt.y);
        }
    }
}

function drawNeedleJS(speed) {
    if (maxSpeed <= 0) return;
    const speedForGauge = Math.min(speed, maxSpeed);
    const needleAngle = startAngleDeg - ((startAngleDeg - endAngleDeg) * (speedForGauge / maxSpeed));

    const startPt = pointOnArcJS(innerRadius, needleAngle);
    const endPt = pointOnArcJS(outerRadius, needleAngle);

    ctx.strokeStyle = 'white';
    ctx.lineWidth = 2 * scaleFactor; // Scale needle width
    ctx.beginPath();
    ctx.moveTo(startPt.x, startPt.y);
    ctx.lineTo(endPt.x, endPt.y);
    ctx.stroke();
}

// --- Main Redraw Function ---
function redrawAll() {
    // 1. Clear Canvas
    ctx.clearRect(0, 0, width, height);
    // Optional: Fill background if needed (though CSS handles it)
    // ctx.fillStyle = 'black';
    // ctx.fillRect(0, 0, width, height);

    // 2. Draw Tachometer Elements
    drawArcOutlineJS();
    drawSpeedTicksJS();
    drawNeedleJS(currentSpeedKmh);

    // 3. Update Text Elements (Handled by HTML/CSS overlays)
    const totalLaps = currentRaceData?.total_laps ?? 0;
    const currentLap = currentRaceData?.current_lap ?? 0;
    lapInfoEl.textContent = `${currentLap}/${totalLaps}`;

    const currentTime = formatTimeJS(currentRaceData?.current_lap_elapsed_seconds);
    currentTimeEl.textContent = `THIS ${currentTime}`;

    const lastTime = formatTimeJS(currentRaceData?.last_lap_time_seconds);
    lastTimeEl.textContent = `LAST ${lastTime}`;

    digitalSpeedEl.textContent = String(Math.floor(currentSpeedKmh));

    // 4. Show/Hide MQTT Status
    mqttStatusEl.style.display = isMqttConnected ? 'none' : 'block';
}

// --- Socket.IO Event Handlers ---
// (These remain the same as before)
socket.on('connect', () => {
    console.log('Connected to server via WebSocket');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    isMqttConnected = false;
    redrawAll();
});

socket.on('initial_state', (state) => {
    console.log('Received initial state:', state);
    currentRaceData = state.raceData;
    currentSpeedKmh = state.speedData?.speed_kmh ?? 0;
    isMqttConnected = state.mqttConnected;
    redrawAll();
});

socket.on('race_update', (data) => {
    currentRaceData = data;
    redrawAll();
});

socket.on('speed_update', (data) => {
    currentSpeedKmh = data?.speed_kmh ?? 0;
    redrawAll();
});

socket.on('mqtt_status', (status) => {
    isMqttConnected = status.connected;
    redrawAll();
});

// Initial draw
redrawAll();