// public/script.js

// --- Map Initialization ---
const map = L.map('map').setView([49.6116, 6.1319], 13); // Centered on Luxembourg City (example)

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}).addTo(map);

// --- DOM Elements ---
const btnDrawStart = document.getElementById('btnDrawStart');
const btnDrawLap = document.getElementById('btnDrawLap');
const btnDrawFinish = document.getElementById('btnDrawFinish');
const btnSaveLines = document.getElementById('btnSaveLines');
const btnClear = document.getElementById('btnClear');
const statusMessage = document.getElementById('statusMessage');
const currentModeSpan = document.getElementById('currentMode');
const totalLapsInput = document.getElementById('totalLaps'); // Get the input element

// --- State Variables ---
let currentDrawingTool = null; // Holds the active Leaflet Draw instance
let currentLineType = null; // 'start', 'lap', or 'finish'
const drawnLines = {
    start: null,
    lap: null,
    finish: null
};
const lineColors = {
    start: 'green',
    lap: 'blue',
    finish: 'red'
};

// --- Helper Functions ---
function setMessage(message, isError = false) {
    statusMessage.textContent = message;
    statusMessage.style.color = isError ? 'red' : 'black';
    console.log(message); // Also log to console
}

function updateCurrentMode(mode) {
    currentModeSpan.textContent = mode;
}

function checkSaveButtonState() {
    // Enable save only if all three lines are drawn
    const canSave = drawnLines.start && drawnLines.lap && drawnLines.finish;
    btnSaveLines.disabled = !canSave;
    if (canSave && !currentDrawingTool) { // Only update message if not actively drawing
        setMessage("All lines drawn or loaded. Ready to save changes.");
    }
}

function clearDrawingTool() {
    if (currentDrawingTool) {
        currentDrawingTool.disable();
        currentDrawingTool = null;
    }
    currentLineType = null;
    updateCurrentMode("None");
    // Reset button styles
    btnDrawStart.classList.remove('active');
    btnDrawLap.classList.remove('active');
    btnDrawFinish.classList.remove('active');
}

function clearAllLines() {
    clearDrawingTool();
    // Remove existing lines from map and state
    Object.keys(drawnLines).forEach(key => {
        if (drawnLines[key]) {
            map.removeLayer(drawnLines[key]);
            drawnLines[key] = null;
        }
    });
    totalLapsInput.value = 0; // Reset laps input
    setMessage("Cleared all lines and config.");
    checkSaveButtonState(); // Disable save button
}

function startDrawing(lineType) {
    clearDrawingTool();
    currentLineType = lineType;
    updateCurrentMode(`Drawing ${lineType.charAt(0).toUpperCase() + lineType.slice(1)} Line`);
    setMessage(`Click two points on the map to draw the ${lineType} line.`);

    if (lineType === 'start') btnDrawStart.classList.add('active');
    if (lineType === 'lap') btnDrawLap.classList.add('active');
    if (lineType === 'finish') btnDrawFinish.classList.add('active');

    currentDrawingTool = new L.Draw.Polyline(map, {
        shapeOptions: { color: lineColors[lineType], weight: 4, opacity: 0.8 },
        allowIntersection: false,
        drawError: { color: '#e1e100', message: 'Click map to place points (max 2).' },
        guidelineDistance: 20,
        maxPoints: 2,
        metric: true,
        showLength: true,
        zIndexOffset: 2000
    });
    currentDrawingTool.enable();
}

// --- Function to Load Initial Config ---
async function loadInitialConfig() {
    setMessage("Loading existing configuration from server...");
    try {
        const response = await fetch('/api/get-config');
        if (!response.ok) {
            throw new Error(`Server responded with status ${response.status}`);
        }
        const config = await response.json();
        console.log("Received config:", config);

        let linesLoadedCount = 0;
        let bounds = L.latLngBounds(); // To zoom map to fit loaded lines

        // Draw Start Line if available
        if (config.start && config.start.length === 2) {
            const latLngs = config.start.map(p => L.latLng(p[0], p[1])); // Ensure correct LatLng format
            drawnLines.start = L.polyline(latLngs, { color: lineColors.start }).addTo(map);
            linesLoadedCount++;
            bounds.extend(latLngs);
        }
        // Draw Finish Line if available
        if (config.finish && config.finish.length === 2) {
            const latLngs = config.finish.map(p => L.latLng(p[0], p[1]));
            drawnLines.finish = L.polyline(latLngs, { color: lineColors.finish }).addTo(map);
            linesLoadedCount++;
            bounds.extend(latLngs);
        }
        // Draw Lap Line if available
        if (config.lap && config.lap.length === 2) {
            const latLngs = config.lap.map(p => L.latLng(p[0], p[1]));
            drawnLines.lap = L.polyline(latLngs, { color: lineColors.lap }).addTo(map);
            linesLoadedCount++;
            bounds.extend(latLngs);
        }

        // Set Total Laps
        totalLapsInput.value = config.totalLaps || 0;

        if (linesLoadedCount > 0) {
             setMessage(`Loaded ${linesLoadedCount} line(s) and total laps config.`);
             if (bounds.isValid()) {
                 map.fitBounds(bounds.pad(0.1)); // Zoom/pan map to show loaded lines + padding
             }
        } else {
            setMessage("No existing configuration found on server. Draw new lines.");
        }
        checkSaveButtonState(); // Enable save if all 3 lines were loaded

    } catch (error) {
        setMessage(`Error loading configuration: ${error.message}`, true);
        console.error("Error fetching config:", error);
        checkSaveButtonState(); // Ensure save is disabled on error
    }
}


// --- Event Listeners ---

// Drawing Buttons
btnDrawStart.addEventListener('click', () => startDrawing('start'));
btnDrawLap.addEventListener('click', () => startDrawing('lap'));
btnDrawFinish.addEventListener('click', () => startDrawing('finish'));

// Clear Button
btnClear.addEventListener('click', clearAllLines); // Use the new clear function

// Save Button
btnSaveLines.addEventListener('click', async () => {
    if (!drawnLines.start || !drawnLines.lap || !drawnLines.finish) {
        setMessage("Cannot save: Please draw all three lines (Start, Lap, Finish).", true);
        return;
    }
    const totalLaps = parseInt(totalLapsInput.value, 10);
    if (isNaN(totalLaps) || totalLaps < 0) {
         setMessage('Invalid value entered for Total Laps.', true);
         return;
    }

    const lineData = {
        start: drawnLines.start.getLatLngs().map(ll => [ll.lat, ll.lng]),
        finish: drawnLines.finish.getLatLngs().map(ll => [ll.lat, ll.lng]),
        lap: drawnLines.lap.getLatLngs().map(ll => [ll.lat, ll.lng]),
        totalLaps: totalLaps
    };

    if (lineData.start.length !== 2 || lineData.finish.length !== 2 || lineData.lap.length !== 2) {
        setMessage("Error: One or more lines do not have exactly two points.", true);
        return;
    }

    setMessage(`Saving lines and config (Total Laps: ${totalLaps})...`, false);
    btnSaveLines.disabled = true;

    try {
        const response = await fetch('/api/set-lines', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(lineData),
        });
        const result = await response.json();
        if (response.ok) setMessage(`Success: ${result.message}`);
        else setMessage(`Error saving: ${result.message || response.statusText}`, true);
    } catch (error) {
        setMessage(`Network or fetch error: ${error.message}`, true);
        console.error("Fetch error:", error);
    } finally {
        checkSaveButtonState(); // Re-check save button state
    }
});

// --- Map Event Listener for Drawing Completion ---
map.on(L.Draw.Event.CREATED, function (event) {
    const layer = event.layer;
    if (!currentLineType) return;

    // Remove the previously drawn line of the same type
    if (drawnLines[currentLineType]) map.removeLayer(drawnLines[currentLineType]);

    drawnLines[currentLineType] = layer;
    map.addLayer(layer);

    setMessage(`Drew ${currentLineType} line.`);
    clearDrawingTool();
    checkSaveButtonState();
});

// --- Initial Setup ---
document.addEventListener('DOMContentLoaded', loadInitialConfig); // Load config when DOM is ready
updateCurrentMode("None");