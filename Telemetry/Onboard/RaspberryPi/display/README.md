# OLED Race Display Client (MQTT Subscriber)

## Description

This Python script runs on a Raspberry Pi (or similar Linux-based device) connected to a 128x64 SSD1309 I2C OLED display. It acts as an MQTT client, subscribing to various topics to receive real-time race data, GPS status, and configuration information. It also reads local wheel speed data from a file and presents a consolidated view on the OLED screen, including a speedometer, lap counter, timers, and status indicators.

## Features

*   **Real-time Speedometer:** Displays current speed using both an analog-style gauge and a digital readout. Speed is calculated based on RPM data read from a local file.
*   **Lap Information:** Shows the current lap number and the total number of laps for the race.
*   **Lap Timers:** Displays the elapsed time for the current lap, the time of the last completed lap, and the configured ideal lap time.
*   **Status Indicators:** Provides visual feedback on the status of:
    *   MQTT connection
    *   GPS fix availability and data freshness
    *   Local speed data freshness
*   **MQTT Integration:** Subscribes to MQTT topics for dynamic updates.
*   **Configuration via MQTT:** Reads essential race parameters like total laps and ideal lap time from retained MQTT messages on specific `config/*` topics.
*   **Local Data Reading:** Reads wheel RPM and timestamp from a designated JSON file (`/tmp/wheel_speed.json`).
*   **Robustness:** Includes automatic reconnection logic for the MQTT connection and handles potential errors during data processing or display updates.

## Dependencies

### Hardware

*   Raspberry Pi or similar Linux computer with Python 3.
*   SSD1309-based 128x64 OLED Display connected via I2C.
*   Network connection (WiFi or Ethernet).
*   Ensure I2C is enabled on the Raspberry Pi (`sudo raspi-config`).

### Software

*   Python 3
*   **Required Python Libraries:** Install using pip:
    ```bash
    pip install paho-mqtt luma.oled Pillow
    ```
*   **Font File:** Requires `DejaVuSans.ttf`. Download it or replace with another `.ttf` font file accessible by the script.

## Configuration

Several parameters need to be configured directly within the `oled_display.py` script:

*   **MQTT Broker Details:**
    *   `MQTT_BROKER`: Address of your MQTT broker (e.g., `"tome.lu"`).
    *   `MQTT_PORT`: Port of your MQTT broker (usually `1883`).
    *   `MQTT_USER`: MQTT username.
    *   `MQTT_PASSWORD`: MQTT password.
*   **MQTT Topics:** Constants defining the topics to subscribe to (e.g., `MQTT_TOPIC_GPS_STATUS`, `MQTT_TOPIC_RACE_LAPS`, `MQTT_CONFIG_BASE_TOPIC`). Ensure these match the topics used by your publisher script(s).
*   **Local Speed File:**
    *   `WHEEL_SPEED_FILE`: Path to the JSON file containing wheel speed data (default: `'/tmp/wheel_speed.json'`).
*   **Vehicle/Sensor Specific:**
    *   `WHEEL_CIRCUMFERENCE_M`: The circumference of the wheel in meters, used for calculating speed from RPM.
*   **Display Settings:**
    *   `serial = i2c(port=1, address=0x3D)`: Adjust the I2C port and address if your display uses different values.
*   **Timing Constants:**
    *   `RECONNECT_DELAY_S`: Delay between MQTT reconnection attempts.
    *   `STALE_DATA_THRESHOLD_S`: How old data (GPS, Speed) can be before being marked stale in the status bar.
    *   `STATUS_UPDATE_INTERVAL_S`: How often to refresh the status indicators.

## MQTT Topics Subscribed

The script subscribes to the following topics:

1.  **`gps/status` (QoS 1):**
    *   **Expected Format:** JSON string.
    *   **Example Payload:** `{"has_fix": true, "fix_quality": 2, "num_satellites": 8, "latitude": 49.6, "longitude": 6.1, "speed_knots": 5.2, "timestamp": 1678886401.5}`
    *   **Usage:** Updates GPS status indicator, potentially used for other display elements in future versions.
2.  **`race/laps` (QoS 1):**
    *   **Expected Format:** JSON string, published on specific race events.
    *   **Example Payloads:**
        *   `{"event": "race_started", "lap_number_starting": 1, "total_laps": 10, "timestamp": 1678886400.0}`
        *   `{"event": "lap_completed", "lap_number": 1, "total_laps": 10, "lap_time_seconds": 58.7, "timestamp": 1678886458.7}`
        *   `{"event": "race_finished", ...}`
    *   **Usage:** Updates current lap, total laps (if provided), last lap time, and resets the current lap timer.
3.  **`config/#` (QoS 1):**
    *   **Expected Format:** Wildcard subscription. Expects *individual*, *retained* messages on sub-topics. The payload is the raw value (not JSON).
    *   **Example Topics & Payloads:**
        *   `config/total_laps`: `"10"` (Plain text integer)
        *   `config/ideal_time`: `"60.5"` (Plain text float/integer representing seconds)
        *   *(Other config topics like `config/start_line` could be added)*
    *   **Usage:** Sets the total number of laps and the ideal lap time displayed. Relies on these messages being published with the `retain=True` flag by the configuration publisher.

## Local Data Source

*   **`/tmp/wheel_speed.json`:**
    *   **Expected Format:** JSON string.
    *   **Example Payload:** `{"rpm": 150.0, "timestamp": 1678886402.123}`
    *   **Usage:** Reads the `rpm` value to calculate and display the current speed. Reads the `timestamp` to determine data freshness for the status indicator. This file must be generated and updated frequently by another process (e.g., a sensor reading script or the `speed_publisher.py` script if adapted).

## Status Bar Indicators

The status bar at the top-center displays three characters indicating system status:

*   **Position 1 (MQTT):**
    *   `M`: Connected to MQTT broker.
    *   `!`: Not connected to MQTT broker.
*   **Position 2 (GPS):**
    *   `G`: GPS has a fix, and data is recent.
    *   `g`: GPS has no fix, or data is recent but lacks a fix.
    *   `?` (or potentially `g` depending on timing): GPS data is stale (not received recently).
*   **Position 3 (Speed):**
    *   `S`: Local speed data (`/tmp/wheel_speed.json`) is recent.
    *   `s`: Local speed data is stale (file not updated recently).

*(Note: Future versions might replace these characters with custom pixel icons for better visual distinction.)*

## Usage

1.  Ensure all hardware is connected and dependencies are installed.
2.  Configure the script parameters (MQTT, file paths, etc.).
3.  Make sure the process generating `/tmp/wheel_speed.json` is running.
4.  Make sure the MQTT publisher script (sending GPS, laps, config data) is running.
5.  Run the script from the command line:
    ```bash
    python oled_display.py
    ```
6.  Press `Ctrl+C` to stop the script gracefully.

## Troubleshooting

*   **Display doesn't turn on:** Check I2C wiring, ensure I2C is enabled (`sudo raspi-config`), verify the I2C address (`i2cdetect -y 1`).
*   **Font error:** Ensure `DejaVuSans.ttf` (or your chosen font) is in the same directory or provide the full path.
*   **MQTT Connection Issues (`!` indicator):** Verify broker address, port, username, password, and network connectivity. Check broker logs.
*   **Stale GPS Data (`g` or `?` indicator):** Ensure the GPS publisher script is running, publishing to the correct `gps/status` topic, and has a GPS fix. Check MQTT connectivity.
*   **Stale Speed Data (`s` indicator):** Ensure the process updating `/tmp/wheel_speed.json` is running and updating the file frequently. Check file permissions.
*   **Lap/Total Laps incorrect:** Verify the publisher script is sending correct data to `race/laps` and `config/total_laps`. Ensure `config/total_laps` was published with `retain=True`. Use an MQTT client (like MQTT Explorer) to inspect messages on the broker.