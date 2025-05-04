# GPS Lap Timer & MQTT Publisher

**Disclaimer:** This README describes the *assumed* functionality of a script that provides the necessary data for the `oled_display.py` client. The actual implementation details might vary.

## Description

This Python script is intended to run on a device connected to a GPS module (e.g., on a vehicle). Its primary functions are:

1.  Read and parse data from a connected GPS module.
2.  Determine the vehicle's position and status (fix, satellites, etc.).
3.  Detect when the vehicle crosses a predefined start/finish line.
4.  Calculate lap times based on line crossings.
5.  Manage the overall race state (e.g., idle, running, finished).
6.  Publish GPS status, race events (lap completion, start/finish), and essential configuration data via MQTT for consumption by other clients like the OLED display.

## Features (Assumed)

*   Interfaces with GPS hardware (e.g., via Serial/USB).
*   Parses standard GPS sentences (like NMEA) to extract position, speed, time, and fix status.
*   Implements geofencing logic to detect crossings of a virtual start/finish line.
*   Calculates lap times accurately.
*   Tracks the current lap number and potentially the race state.
*   Publishes structured data to distinct MQTT topics.
*   Publishes initial configuration data (total laps, ideal time, start line definition) as **retained** MQTT messages.

## Dependencies (Likely)

### Hardware

*   Raspberry Pi or similar Linux computer.
*   GPS Module (e.g., Adafruit Ultimate GPS, U-blox series) connected via Serial (UART) or USB.
*   Network connection (WiFi or Ethernet).

### Software

*   Python 3
*   **Required Python Libraries:**
    ```bash
    pip install paho-mqtt pyserial # Or specific library for your GPS module
    # Potentially needed for geofencing:
    # pip install shapely
    # Potentially needed for NMEA parsing:
    # pip install pynmea2
    # Or use gpsd and its client library:
    # sudo apt-get install gpsd gpsd-clients python3-gps
    # pip install gpsd-py3
    ```

## Configuration (Likely)

The script would require configuration, potentially via command-line arguments, a configuration file, or hardcoded constants:

*   **MQTT Broker Details:** Address, port, username, password.
*   **GPS Device:** Serial port (e.g., `/dev/ttyS0`, `/dev/ttyAMA0`, `/dev/serial0`, `/dev/ttyUSB0`), baud rate. Or configuration for `gpsd`.
*   **Start/Finish Line:** Coordinates defining the line (e.g., two sets of latitude/longitude points).
*   **Race Parameters:**
    *   Total number of laps for the race.
    *   Ideal lap time (in seconds).
    *   These might be loaded from a file or set when the script starts.

## MQTT Topics Published

This script is expected to publish to the following topics:

1.  **`gps/status` (QoS 1, Retain: False):**
    *   **Format:** JSON string.
    *   **Payload Example:** `{"has_fix": true, "fix_quality": 2, "num_satellites": 8, "latitude": 49.6, "longitude": 6.1, "speed_knots": 5.2, "timestamp": 1678886401.5}`
    *   **Frequency:** Published regularly (e.g., 1-10 Hz) whenever new GPS data is available.
2.  **`race/laps` (QoS 1, Retain: False):**
    *   **Format:** JSON string.
    *   **Payload Examples:**
        *   `{"event": "race_started", "lap_number_starting": 1, "total_laps": 10, "timestamp": 1678886400.0}`
        *   `{"event": "lap_completed", "lap_number": 1, "total_laps": 10, "lap_time_seconds": 58.7, "timestamp": 1678886458.7}`
        *   `{"event": "race_finished", ...}`
    *   **Frequency:** Published only when specific race events occur (start, lap completion, finish).
3.  **`config/total_laps` (QoS 1, Retain: True):**
    *   **Format:** Plain text integer.
    *   **Payload Example:** `"10"`
    *   **Frequency:** Published **once** when the script starts or when the configuration is set. **Crucially, `retain=True` must be set.**
4.  **`config/ideal_time` (QoS 1, Retain: True):**
    *   **Format:** Plain text float or integer (seconds).
    *   **Payload Example:** `"60.5"`
    *   **Frequency:** Published **once** when the script starts or configuration is set. **`retain=True` must be set.**
5.  **`config/start_line` (QoS 1, Retain: True):** (Optional but recommended)
    *   **Format:** JSON string (or other suitable format).
    *   **Payload Example:** `{"lat1": 49.6001, "lon1": 6.1001, "lat2": 49.6002, "lon2": 6.1002}`
    *   **Frequency:** Published **once** when the script starts. **`retain=True` must be set.**

**IMPORTANT:** Publishing the `config/*` topics with `retain=True` is essential so that clients like the OLED display can receive the configuration immediately upon connecting and subscribing, even if they start after the publisher.

## Usage (Assumed)

1.  Connect the GPS module to the device.
2.  Install dependencies.
3.  Configure the script (MQTT, GPS device, Start/Finish line, Race parameters).
4.  Run the script:
    ```bash
    python gps_logger.py
    ```
5.  The script should connect to the GPS, start processing data, and publish to MQTT.

## Notes

*   The accuracy of lap timing depends heavily on the GPS accuracy, update rate, and the algorithm used for line crossing detection.
*   Consider filtering GPS data to handle noise or temporary signal loss.
*   Error handling for GPS communication and MQTT publishing is important.