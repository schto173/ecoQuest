// public/script.js

document.addEventListener('DOMContentLoaded', () => {
    // --- Map Initialization ---
    const map = L.map('map').setView([49.6116, 6.1319], 13); // Centered on Luxembourg City (example)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // --- DOM Elements ---
    const btnDrawStart = document.getElementById('btnDrawStart');
    const btnDrawLap = document.getElementById('btnDrawLap');
    const btnDrawFinish = document.getElementById('btnDrawFinish');
    const btnSaveLines = document.getElementById('btnSaveLines');
    const btnClear = document.getElementById('btnClear');
    const statusMessage = document.getElementById('statusMessage');
    const currentModeSpan = document.getElementById('currentMode');
    const totalLapsInput = document.getElementById('totalLaps');
    const idealTimeInput = document.getElementById('idealTime'); // Get the ideal time input

    // --- State Variables ---
    let currentDrawingTool = null; // Holds the active Leaflet Draw instance
    let currentLineType = null; // 'start', 'lap', or 'finish'
    const drawnLines = { // Store the Leaflet layer objects
        start: null,
        lap: null,
        finish: null
    };
    const lineColors = {
        start: 'green',
        lap: 'blue',
        finish: 'red'
    };
    const defaultIdealTime = 60; // Define default for reset/load failure

    // --- Feature Group for Drawn Items ---
    // Use a FeatureGroup to make clearing layers easier
    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    // --- Helper Functions ---
    function setMessage(message, isError = false) {
        statusMessage.textContent = message;
        statusMessage.style.color = isError ? 'red' : 'black';
        if (isError) {
            console.error("Status:", message);
        } else {
            console.log("Status:", message);
        }
    }

    function updateCurrentMode(mode) {
        currentModeSpan.textContent = mode;
        // Visually indicate active button
        [btnDrawStart, btnDrawLap, btnDrawFinish].forEach(btn => {
            btn.classList.remove('active');
            if (btn.id.toLowerCase().includes(mode.toLowerCase().split(' ')[1])) {
                 btn.classList.add('active');
            }
        });
         if (mode === 'None') {
             btnDrawStart.classList.remove('active');
             btnDrawLap.classList.remove('active');
             btnDrawFinish.classList.remove('active');
         }
    }

    function checkSaveButtonState() {
        // Enable save only if all three lines are drawn (exist in drawnLines)
        const canSave = drawnLines.start && drawnLines.lap && drawnLines.finish;
        btnSaveLines.disabled = !canSave;

        // Update status message only if not actively drawing
        if (!currentDrawingTool) {
            if (canSave) {
                setMessage("All lines drawn or loaded. Ready to save changes.");
            } else {
                 // Figure out which lines are missing
                 let missing = [];
                 if (!drawnLines.start) missing.push("Start");
                 if (!drawnLines.lap) missing.push("Lap");
                 if (!drawnLines.finish) missing.push("Finish");
                 if (missing.length > 0) {
                    setMessage(`Draw ${missing.join(', ')} line(s) to enable saving.`);
                 } else {
                     // Should not happen if canSave is false, but as a fallback
                     setMessage("Draw Start, Lap, and Finish lines.");
                 }
            }
        }
    }

    function clearDrawingTool() {
        if (currentDrawingTool) {
            currentDrawingTool.disable();
            currentDrawingTool = null;
        }
        currentLineType = null;
        updateCurrentMode("None"); // Update button styles and text
    }

    function clearAllLocalState() {
        clearDrawingTool();
        // Remove layers from the map via the feature group
        drawnItems.clearLayers();
        // Reset state variables
        drawnLines.start = null;
        drawnLines.lap = null;
        drawnLines.finish = null;
        // Reset input fields to defaults
        totalLapsInput.value = 0;
        idealTimeInput.value = defaultIdealTime; // Reset to default 60
        setMessage("Cleared local lines and config. Load from server or draw new lines.");
        checkSaveButtonState(); // Disable save button
    }

    function startDrawing(lineType) {
        // If already drawing this type, cancel it
        if (currentDrawingTool && currentLineType === lineType) {
            clearDrawingTool();
            checkSaveButtonState(); // Update status message
            return;
        }

        clearDrawingTool(); // Clear any previous tool
        currentLineType = lineType;
        const typeName = lineType.charAt(0).toUpperCase() + lineType.slice(1);
        updateCurrentMode(`Drawing ${typeName}`);
        setMessage(`Click two points on the map to draw the ${typeName} line. Click button again to cancel.`);

        // Create and enable the drawing tool
        currentDrawingTool = new L.Draw.Polyline(map, {
            shapeOptions: { color: lineColors[lineType], weight: 4, opacity: 0.8 },
            allowIntersection: false, // Don't allow self-intersection
            drawError: { color: '#e1e100', message: 'Click map to place points (max 2).' },
            guidelineDistance: 20,
            maxPoints: 2, // Only allow 2 points for a line
            metric: true, // Use metric units
            showLength: true, // Show length while drawing
            zIndexOffset: 2000 // Make sure drawing line is on top
        });
        currentDrawingTool.enable();
    }

    // --- Function to Load Initial Config ---
    async function loadInitialConfig() {
        setMessage("Loading existing configuration from server...");
        btnSaveLines.disabled = true; // Disable save during load
        try {
            const response = await fetch('/api/get-config');
            if (!response.ok) {
                let errorMsg = `Server responded with status ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMsg = errorData.message || errorMsg;
                } catch (e) { /* Ignore if response body is not JSON */ }
                throw new Error(errorMsg);
            }
            const config = await response.json();
            console.log("Received config from server:", config);

            clearAllLocalState(); // Clear previous state before loading new

            let linesLoadedCount = 0;
            let bounds = L.latLngBounds(); // To zoom map to fit loaded lines

            // Helper to draw a line from config data
            const drawLineFromConfig = (lineData, type) => {
                if (lineData && lineData.length === 2) {
                    try {
                        // Ensure data is in LatLng format for Leaflet
                        const latLngs = lineData.map(p => L.latLng(p[0], p[1]));
                        const line = L.polyline(latLngs, { color: lineColors[type] });
                        drawnItems.addLayer(line); // Add to feature group
                        drawnLines[type] = line; // Store reference
                        linesLoadedCount++;
                        bounds.extend(latLngs); // Extend bounds to include this line
                        return true;
                    } catch (e) {
                         console.error(`Error creating ${type} line from data:`, lineData, e);
                         setMessage(`Error processing ${type} line data from server.`, true);
                         return false;
                    }
                }
                return false;
            };

            // Draw lines if available
            drawLineFromConfig(config.start, 'start');
            drawLineFromConfig(config.finish, 'finish');
            drawLineFromConfig(config.lap, 'lap');

            // Set Total Laps - Use server value if valid, otherwise default to 0
            totalLapsInput.value = (config.totalLaps !== null && config.totalLaps !== undefined && !isNaN(config.totalLaps))
                                   ? config.totalLaps
                                   : 0;

            // Set Ideal Time - Use server value if valid, otherwise default to 60
            idealTimeInput.value = (config.idealTime !== null && config.idealTime !== undefined && !isNaN(config.idealTime))
                                  ? config.idealTime
                                  : defaultIdealTime;

            if (linesLoadedCount > 0) {
                 setMessage(`Loaded ${linesLoadedCount} line(s) and config (Laps: ${totalLapsInput.value}, Ideal Time: ${idealTimeInput.value}s).`);
                 if (bounds.isValid()) {
                     map.fitBounds(bounds.pad(0.1)); // Zoom/pan map to show loaded lines + padding
                 }
            } else {
                setMessage("No existing line configuration found on server. Draw new lines.");
            }

        } catch (error) {
            setMessage(`Error loading configuration: ${error.message}`, true);
            console.error("Error fetching or processing config:", error);
            // Keep inputs at their default values on error
            clearAllLocalState(); // Reset to defaults on load error
        } finally {
             checkSaveButtonState(); // Update save button state and status message
        }
    }


    // --- Event Listeners ---

    // Drawing Buttons
    btnDrawStart.addEventListener('click', () => startDrawing('start'));
    btnDrawLap.addEventListener('click', () => startDrawing('lap'));
    btnDrawFinish.addEventListener('click', () => startDrawing('finish'));

    // Clear Button
    btnClear.addEventListener('click', clearAllLocalState); // Use the new clear function

    // Save Button
    btnSaveLines.addEventListener('click', async () => {
        // Double-check that all lines are drawn
        if (!drawnLines.start || !drawnLines.lap || !drawnLines.finish) {
            setMessage("Cannot save: Please ensure Start, Lap, and Finish lines are drawn.", true);
            checkSaveButtonState(); // Update message if needed
            return;
        }

        // --- Read and Validate Inputs ---
        const totalLaps = parseInt(totalLapsInput.value, 10);
        const idealTime = parseFloat(idealTimeInput.value); // Read ideal time as float

        if (isNaN(totalLaps) || totalLaps < 0 || !Number.isInteger(totalLaps)) {
             setMessage('Invalid value for Total Laps. Must be a whole number >= 0.', true);
             return;
        }
        if (isNaN(idealTime) || idealTime < 0) {
             setMessage('Invalid value for Ideal Lap Time. Must be a number >= 0.', true);
             return;
        }

        // --- Prepare Data Payload ---
        // Get LatLngs and map to [lat, lon] format for the backend/MQTT
        const getLineCoords = (line) => line.getLatLngs().map(ll => [ll.lat, ll.lng]);

        const lineData = {
            start: getLineCoords(drawnLines.start),
            finish: getLineCoords(drawnLines.finish),
            lap: getLineCoords(drawnLines.lap),
            totalLaps: totalLaps,
            idealTime: idealTime // Include ideal time
        };

        // --- Log data being sent ---
        console.log('Data being sent to /api/set-lines:', JSON.stringify(lineData, null, 2));

        // --- Send Request ---
        setMessage(`Saving lines and config (Laps: ${totalLaps}, Ideal Time: ${idealTime}s)...`, false);
        btnSaveLines.disabled = true; // Disable button during save attempt

        try {
            const response = await fetch('/api/set-lines', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(lineData),
            });

            const result = await response.json(); // Try to parse JSON response body

            if (response.ok) {
                setMessage(`Success: ${result.message}`);
                console.log("Save successful:", result);
            } else {
                // Use message from server response if available, otherwise use statusText
                const errorMsg = result.message || response.statusText || `HTTP error ${response.status}`;
                setMessage(`Error saving: ${errorMsg}`, true);
                console.error("Save failed:", response.status, result);
            }
        } catch (error) {
            // Network error or error parsing JSON response
            setMessage(`Network or fetch error: ${error.message}`, true);
            console.error("Fetch error during save:", error);
        } finally {
            // Re-enable save button ONLY if all lines are still considered drawn
            // (which they should be unless something went very wrong)
            checkSaveButtonState();
        }
    });

    // --- Map Event Listener for Drawing Completion ---
    map.on(L.Draw.Event.CREATED, function (event) {
        const layer = event.layer;
        const type = event.layerType; // Type is 'polyline'

        if (!currentLineType || type !== 'polyline') {
             console.warn("Draw event created, but no line type active or not a polyline.", event);
             clearDrawingTool(); // Stop drawing mode just in case
             return; // Ignore if not drawing a polyline
        }

        // Check if it's a polyline with exactly 2 points
        if (layer.getLatLngs().length === 2) {
            // Remove the previously drawn line of the same type, if any
            if (drawnLines[currentLineType]) {
                drawnItems.removeLayer(drawnLines[currentLineType]); // Remove from group
            }
            // Store and add the new layer
            drawnLines[currentLineType] = layer;
            drawnItems.addLayer(layer); // Add the new layer to the group
            setMessage(`Drew ${currentLineType} line.`);
        } else {
            // Should not happen with maxPoints: 2, but handle defensively
            setMessage(`Error: Drawn shape is not a valid 2-point line. Please try drawing the ${currentLineType} line again.`, true);
            console.warn("Incorrect number of points created:", event);
        }

        clearDrawingTool(); // Stop drawing mode
        checkSaveButtonState(); // Check if save is now possible and update status
    });

    // --- Initial Setup ---
    loadInitialConfig(); // Load config when DOM is ready
    updateCurrentMode("None"); // Set initial mode text
    // checkSaveButtonState is called at the end of loadInitialConfig

}); // End DOMContentLoaded