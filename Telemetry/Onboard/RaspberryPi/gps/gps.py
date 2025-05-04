#!/usr/bin/env python3

import math
import json
import time
from datetime import datetime, timezone # Added timezone
import paho.mqtt.client as mqtt
import serial
import pynmea2
import threading
import signal
import sys
# import os # Keep os import if needed elsewhere (currently not)

# --- Constants ---
SERIAL_PORT = '/dev/ttyS0' # Or '/dev/serial0' or your specific port
BAUD_RATE = 115200
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_USERNAME = "eco"
MQTT_PASSWORD = "marathon" # Consider environment variables or config file
MQTT_CLIENT_ID = "gps_monitor_pi"

# --- MQTT Topics ---
MQTT_TOPIC_POSITION = "gps/position" # Lat, Lon, Speed(kmh), Heading, Alt, Timestamp
MQTT_TOPIC_GPS_STATUS = "gps/status"   # Fix status, quality, satellites (Retained)
MQTT_TOPIC_LAPS = "race/laps"          # Lap completion events (Not Retained)

# --- Configuration Topics ---
MQTT_TOPIC_CONFIG_START = "config/start_line"
MQTT_TOPIC_CONFIG_FINISH = "config/finish_line"
MQTT_TOPIC_CONFIG_LAP = "config/lap_line"
MQTT_TOPIC_CONFIG_TOTAL_LAPS = "config/total_laps"

# --- Proximity Check Radius ---
PROXIMITY_RADIUS_METERS = 25.0

# --- Serial Error Handling ---
serial_read_error_count = 0
MAX_SERIAL_READ_ERRORS_BEFORE_RECONNECT = 10

# --- Speed Conversion ---
KNOTS_TO_KMH = 1.852

# --- Global Variables ---
gps_state = {
    "latitude": None,
    "longitude": None,
    "altitude": None,
    "timestamp": None,     # ISO Format UTC
    "speed_knots": None,   # Store raw knots internally
    "heading": None,
    "has_fix": False,
    "fix_quality": 0,
    "num_satellites": 0,
    "error_count": 0,
    "last_valid_time": None,
    "previous_position": None, # Store as (lon, lat)
}

race_state = {
    "start_line_p1": None, # Store as (lon, lat)
    "start_line_p2": None, # Store as (lon, lat)
    "finish_line_p1": None,
    "finish_line_p2": None,
    "lap_line_p1": None,
    "lap_line_p2": None,
    "total_laps": 0,
    "current_lap": 0,
    "current_lap_start_time": None, # Epoch seconds (internal use)
    "race_finished": False,
    # Internal debounce state
    "_last_line_crossed_type": None,
    "_last_cross_time_epoch": None,
}

mqtt_client = None
serial_connection = None
shutdown_flag = threading.Event()
last_status_publish_time = 0 # Track time for periodic status updates

# --- Geometric Helper Functions (Unchanged) ---
def on_segment(p, q, r):
    """Check if point q lies on segment pr. Points are (lon, lat)."""
    if p is None or q is None or r is None: return False # Added None check
    if not (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
            min(p[1], r[1]) <= q[1] <= max(p[1], r[1])):
        return False
    val = (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])
    return abs(val) < 1e-9

def orientation(p, q, r):
    """Find orientation of ordered triplet (p, q, r). Points are (lon, lat)."""
    if p is None or q is None or r is None: return 0 # Treat None as collinear/indeterminate
    val = (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])
    if abs(val) < 1e-9: return 0
    return 1 if val > 0 else -1

def intersect(p1, q1, p2, q2):
    """Check if line segment 'p1q1' intersects line segment 'p2q2'. Points are (lon, lat)."""
    if p1 is None or q1 is None or p2 is None or q2 is None: return False
    o1 = orientation(p1, q1, p2); o2 = orientation(p1, q1, q2)
    o3 = orientation(p2, q2, p1); o4 = orientation(p2, q2, q1)
    if o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0:
        if o1 != o2 and o3 != o4: return True
    if o1 == 0 and on_segment(p1, p2, q1): return True
    if o2 == 0 and on_segment(p1, q2, q1): return True
    if o3 == 0 and on_segment(p2, p1, q2): return True
    if o4 == 0 and on_segment(p2, q1, q2): return True
    return False

def calculate_midpoint(p1, p2):
    """Calculates the midpoint between two points (lon, lat)."""
    if p1 is None or p2 is None: return None
    return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)

def haversine_distance(p1, p2):
    """Calculate the great-circle distance between two points (lon, lat). Returns meters."""
    if p1 is None or p2 is None: return float('inf')
    lon1, lat1, lon2, lat2 = map(math.radians, [p1[0], p1[1], p2[0], p2[1]])
    dlon = lon2 - lon1; dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return 6371000 * c
# --- End Geometric Helpers ---

# --- Simplified Crossing Logic with Proximity (Unchanged) ---
def is_crossing_line_with_proximity(line_p1, line_p2, prev_pos, curr_pos, radius_meters):
    """Checks intersection and proximity to line center."""
    if prev_pos is None or curr_pos is None or line_p1 is None or line_p2 is None: return False
    if prev_pos == curr_pos: return False
    if not intersect(line_p1, line_p2, prev_pos, curr_pos): return False
    line_center = calculate_midpoint(line_p1, line_p2)
    if line_center is None: return False
    dist_prev_to_center = haversine_distance(prev_pos, line_center)
    dist_curr_to_center = haversine_distance(curr_pos, line_center)
    return (dist_prev_to_center <= radius_meters) or (dist_curr_to_center <= radius_meters)
# --- End Crossing Logic ---

# --- MQTT Callback Functions (Config Handling Unchanged) ---
def on_connect(client, userdata, flags, rc, properties=None):
    """Callback for when the client connects to the MQTT broker."""
    if rc == 0:
        print("Successfully connected to MQTT Broker.")
        config_topics = [
            (MQTT_TOPIC_CONFIG_START, 2), (MQTT_TOPIC_CONFIG_FINISH, 2),
            (MQTT_TOPIC_CONFIG_LAP, 2), (MQTT_TOPIC_CONFIG_TOTAL_LAPS, 2)
        ]
        client.subscribe(config_topics)
        print(f"Subscribed to config topics: {[t[0] for t in config_topics]}")
        # Publish initial GPS status immediately on connect
        publish_gps_status()
    else:
        print(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, flags, reason_code=None, properties=None):
    print(f"Disconnected from MQTT Broker. Reason Code: {reason_code}")
    if reason_code != 0:
         print("Unexpected disconnection. Client will attempt to reconnect automatically.")

def on_message(client, userdata, msg):
    """Callback for received config messages."""
    global race_state
    topic = msg.topic
    try:
        payload = msg.payload.decode('utf-8')
        if topic == MQTT_TOPIC_CONFIG_START:
            data = json.loads(payload); race_state["start_line_p1"] = tuple(data['p1']); race_state["start_line_p2"] = tuple(data['p2'])
            print(f"Updated Start Line: {race_state['start_line_p1']} -> {race_state['start_line_p2']}")
        elif topic == MQTT_TOPIC_CONFIG_FINISH:
            data = json.loads(payload); race_state["finish_line_p1"] = tuple(data['p1']); race_state["finish_line_p2"] = tuple(data['p2'])
            print(f"Updated Finish Line: {race_state['finish_line_p1']} -> {race_state['finish_line_p2']}")
        elif topic == MQTT_TOPIC_CONFIG_LAP:
            data = json.loads(payload); race_state["lap_line_p1"] = tuple(data['p1']); race_state["lap_line_p2"] = tuple(data['p2'])
            print(f"Updated Lap Line: {race_state['lap_line_p1']} -> {race_state['lap_line_p2']}")
        elif topic == MQTT_TOPIC_CONFIG_TOTAL_LAPS:
            try:
                laps = int(payload)
                if laps >= 0: race_state["total_laps"] = laps; print(f"Updated Total Laps: {race_state['total_laps']}")
                else: print(f"Warning: Received invalid total laps value: {payload}")
            except ValueError: print(f"Warning: Could not parse total laps value: {payload}")
    except json.JSONDecodeError: print(f"Error decoding JSON from topic {topic}: {payload}")
    except KeyError as e: print(f"Error processing message from topic {topic}: Missing key {e}")
    except Exception as e: print(f"An unexpected error occurred in on_message for topic {topic}: {e}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    pass # Optional logging
# --- End MQTT Callbacks ---

# --- GPS Data Processing (Unchanged logic, uses speed_knots internally) ---
def get_utc_iso_timestamp():
    """Returns the current UTC time in ISO 8601 format with Z."""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def update_from_nmea(nmea_sentence):
    """Parses NMEA sentence and updates gps_state. Returns True if state changed."""
    global gps_state
    updated = False
    initial_fix_status = gps_state["has_fix"]
    initial_quality = gps_state["fix_quality"]
    initial_sats = gps_state["num_satellites"]

    try:
        msg = pynmea2.parse(nmea_sentence)
        current_valid = gps_state["longitude"] is not None and gps_state["latitude"] is not None
        if current_valid:
            gps_state["previous_position"] = (gps_state["longitude"], gps_state["latitude"])

        # --- Process GGA ---
        if isinstance(msg, pynmea2.types.talker.GGA):
            new_fix_quality = msg.gps_qual if msg.gps_qual is not None else 0
            gps_state["fix_quality"] = new_fix_quality
            gps_state["num_satellites"] = int(msg.num_sats) if msg.num_sats else 0
            gps_state["altitude"] = msg.altitude if hasattr(msg, 'altitude') else gps_state["altitude"] # Keep last known if not present

            if new_fix_quality > 0 and msg.latitude is not None and msg.longitude is not None:
                gps_state["latitude"] = msg.latitude
                gps_state["longitude"] = msg.longitude
                gps_state["has_fix"] = True
                if hasattr(msg, 'timestamp') and msg.timestamp:
                     # Prefer RMC's datetime, but use GGA time if RMC hasn't provided full date yet
                     if gps_state["timestamp"] is None or len(gps_state["timestamp"]) < 15:
                         today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                         # Ensure microseconds are handled correctly for ISO format
                         time_str = msg.timestamp.strftime('%H:%M:%S.%f')
                         gps_state["timestamp"] = f"{today_date}T{time_str[:-3]}Z" # Milliseconds precision
                elif gps_state["timestamp"] is None: # Absolute fallback
                     gps_state["timestamp"] = get_utc_iso_timestamp()
                gps_state["last_valid_time"] = time.time()
                updated = True
            else:
                gps_state["has_fix"] = False
                # Keep last known lat/lon/alt

        # --- Process RMC ---
        elif isinstance(msg, pynmea2.types.talker.RMC):
             if msg.status == 'A' and msg.latitude is not None and msg.longitude is not None:
                 gps_state["latitude"] = msg.latitude
                 gps_state["longitude"] = msg.longitude
                 gps_state["speed_knots"] = msg.spd_over_grnd if msg.spd_over_grnd is not None else 0.0
                 gps_state["heading"] = msg.true_course if hasattr(msg, 'true_course') and msg.true_course is not None else gps_state["heading"] # Keep last known

                 if hasattr(msg, 'datetime') and msg.datetime:
                     try:
                         # Use combined date and time from RMC for best timestamp
                         gps_state["timestamp"] = msg.datetime.replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                     except Exception:
                         gps_state["timestamp"] = get_utc_iso_timestamp() # Fallback UTC now
                 elif gps_state["timestamp"] is None: # Fallback if RMC has no datetime
                      gps_state["timestamp"] = get_utc_iso_timestamp()

                 gps_state["has_fix"] = True
                 gps_state["last_valid_time"] = time.time()
                 if gps_state["fix_quality"] == 0: gps_state["fix_quality"] = 1 # Basic fix
                 updated = True
             elif msg.status == 'V':
                 gps_state["has_fix"] = False
                 gps_state["fix_quality"] = 0
                 gps_state["speed_knots"] = 0.0
                 # Keep last known lat/lon/alt/heading

        # Determine if status actually changed for publication trigger
        status_changed = (gps_state["has_fix"] != initial_fix_status or
                          gps_state["fix_quality"] != initial_quality or
                          gps_state["num_satellites"] != initial_sats)

        # Ensure previous_position is set if we just got the *first* valid fix
        if updated and gps_state["has_fix"] and gps_state["previous_position"] is None and current_valid:
             gps_state["previous_position"] = (gps_state["longitude"], gps_state["latitude"])

    except pynmea2.ParseError:
        gps_state["error_count"] += 1; status_changed = False
    except AttributeError as e:
        print(f"NMEA Attribute Error: {e} in sentence: {nmea_sentence.strip()}"); gps_state["error_count"] += 1; status_changed = False
    except Exception as e:
        print(f"Unexpected error parsing NMEA: {e}"); gps_state["error_count"] += 1; status_changed = False

    # Return True only if relevant status fields changed
    return status_changed
# --- End GPS Processing ---


# --- Lap Timing Logic (Unchanged) ---
def update_lap_status():
    """Checks for line crossings and publishes lap events to MQTT."""
    global race_state, gps_state, mqtt_client
    if race_state["race_finished"] or not gps_state["has_fix"]: return
    if race_state["total_laps"] <= 0: return
    current_pos = (gps_state["longitude"], gps_state["latitude"])
    prev_pos = gps_state["previous_position"]
    if current_pos is None or prev_pos is None: return

    now_epoch = time.time()
    now_iso = get_utc_iso_timestamp()
    crossed_line_type_this_update = None
    debounce_seconds = 2.0

    # --- Check Start Line ---
    if race_state["current_lap"] == 0 and race_state["start_line_p1"] and race_state["start_line_p2"]:
        if is_crossing_line_with_proximity(race_state["start_line_p1"], race_state["start_line_p2"], prev_pos, current_pos, PROXIMITY_RADIUS_METERS):
            if race_state["_last_line_crossed_type"] != 'start' or (now_epoch - (race_state.get("_last_cross_time_epoch", 0) or 0)) > debounce_seconds:
                print(f"--- Crossed START Line at {now_iso} ---")
                race_state["current_lap"] = 1; race_state["current_lap_start_time"] = now_epoch
                race_state["_last_line_crossed_type"] = 'start'; race_state["_last_cross_time_epoch"] = now_epoch
                crossed_line_type_this_update = 'start'
                lap_payload = {"event": "race_started", "start_time_iso": now_iso, "lap_number_starting": 1, "total_laps": race_state["total_laps"]}
                publish_to_mqtt(MQTT_TOPIC_LAPS, lap_payload, qos=1, retain=False)

    # --- Check Lap Line ---
    elif 0 < race_state["current_lap"] <= race_state["total_laps"] and race_state["lap_line_p1"] and race_state["lap_line_p2"]:
        is_finish_line_same_as_lap = (race_state["lap_line_p1"] == race_state["finish_line_p1"] and race_state["lap_line_p2"] == race_state["finish_line_p2"])
        should_check_lap = not (race_state["current_lap"] == race_state["total_laps"] and is_finish_line_same_as_lap)
        if should_check_lap and is_crossing_line_with_proximity(race_state["lap_line_p1"], race_state["lap_line_p2"], prev_pos, current_pos, PROXIMITY_RADIUS_METERS):
            if race_state["_last_line_crossed_type"] != 'lap' or (now_epoch - (race_state.get("_last_cross_time_epoch", 0) or 0)) > debounce_seconds:
                lap_just_completed = race_state["current_lap"]
                print(f"--- Crossed LAP Line at {now_iso} (Completed Lap {lap_just_completed}) ---")
                lap_duration = None; start_time_iso = None
                if race_state["current_lap_start_time"] is not None:
                    lap_duration = now_epoch - race_state["current_lap_start_time"]
                    start_time_iso = datetime.fromtimestamp(race_state["current_lap_start_time"], timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                    print(f"    Lap {lap_just_completed} Time: {lap_duration:.2f}s")
                lap_payload = {"event": "lap_completed", "lap_number": lap_just_completed, "start_time_iso": start_time_iso, "end_time_iso": now_iso, "duration_seconds": lap_duration, "total_laps": race_state["total_laps"]}
                publish_to_mqtt(MQTT_TOPIC_LAPS, lap_payload, qos=1, retain=False)
                race_state["current_lap"] += 1; race_state["current_lap_start_time"] = now_epoch
                race_state["_last_line_crossed_type"] = 'lap'; race_state["_last_cross_time_epoch"] = now_epoch
                crossed_line_type_this_update = 'lap'
                if race_state["current_lap"] > race_state["total_laps"]:
                    print("--- RACE FINISHED (by completing last lap via Lap Line) ---")
                    race_state["race_finished"] = True
                    finish_payload = {"event": "race_finished", "finish_time_iso": now_iso, "final_lap_number": lap_just_completed, "final_lap_duration_seconds": lap_duration}
                    publish_to_mqtt(MQTT_TOPIC_LAPS, finish_payload, qos=1, retain=False)

    # --- Check Finish Line ---
    if race_state["current_lap"] == race_state["total_laps"] and not race_state["race_finished"] and race_state["finish_line_p1"] and race_state["finish_line_p2"]:
        is_finish_line_same_as_lap = (race_state["lap_line_p1"] == race_state["finish_line_p1"] and race_state["lap_line_p2"] == race_state["finish_line_p2"])
        if crossed_line_type_this_update != 'lap' or is_finish_line_same_as_lap:
            if is_crossing_line_with_proximity(race_state["finish_line_p1"], race_state["finish_line_p2"], prev_pos, current_pos, PROXIMITY_RADIUS_METERS):
                if race_state["_last_line_crossed_type"] != 'finish' or (now_epoch - (race_state.get("_last_cross_time_epoch", 0) or 0)) > debounce_seconds:
                    print(f"--- Crossed FINISH Line at {now_iso} ---")
                    lap_just_completed = race_state["current_lap"]
                    lap_duration = None; start_time_iso = None
                    if race_state["current_lap_start_time"] is not None:
                         lap_duration = now_epoch - race_state["current_lap_start_time"]
                         start_time_iso = datetime.fromtimestamp(race_state["current_lap_start_time"], timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                         print(f"    Final Lap ({lap_just_completed}) Time: {lap_duration:.2f}s")
                    race_state["race_finished"] = True
                    race_state["_last_line_crossed_type"] = 'finish'; race_state["_last_cross_time_epoch"] = now_epoch
                    crossed_line_type_this_update = 'finish'
                    lap_payload = {"event": "lap_completed", "lap_number": lap_just_completed, "start_time_iso": start_time_iso, "end_time_iso": now_iso, "duration_seconds": lap_duration, "total_laps": race_state["total_laps"], "race_finished_flag": True}
                    publish_to_mqtt(MQTT_TOPIC_LAPS, lap_payload, qos=1, retain=False)
                    finish_payload = {"event": "race_finished", "finish_time_iso": now_iso, "final_lap_number": lap_just_completed, "final_lap_duration_seconds": lap_duration}
                    publish_to_mqtt(MQTT_TOPIC_LAPS, finish_payload, qos=1, retain=False)
# --- End Lap Timing ---


# --- Publishing Functions (Revised position data) ---

def publish_to_mqtt(topic, payload_dict, qos=0, retain=False):
    """Helper function to publish JSON payload to a topic."""
    global mqtt_client
    if mqtt_client and mqtt_client.is_connected():
        try:
            # Ensure all data is JSON serializable (esp. timestamps)
            payload_json = json.dumps(payload_dict, default=str) # Use default=str as fallback
            result = mqtt_client.publish(topic, payload_json, qos=qos, retain=retain)
            # print(f"Published to {topic}: {payload_json}") # Debug
        except TypeError as e:
            print(f"Error serializing JSON for topic {topic}: {e} - Payload: {payload_dict}")
        except Exception as e:
            print(f"Error publishing to MQTT topic {topic}: {e}")

def publish_position_data():
    """Publishes core position data (speed in km/h) to MQTT_TOPIC_POSITION."""
    global gps_state
    # Only publish if we have a valid fix and essential data
    if gps_state["has_fix"] and gps_state["latitude"] is not None and gps_state["longitude"] is not None:
        # Convert speed to km/h for publishing
        speed_kmh = None
        if gps_state["speed_knots"] is not None:
            speed_kmh = round(gps_state["speed_knots"] * KNOTS_TO_KMH, 2) # Round to 2 decimal places

        payload = {
            "latitude": gps_state["latitude"],
            "longitude": gps_state["longitude"],
            "altitude": gps_state["altitude"],
            "speed_kmh": speed_kmh, # Publish speed in km/h
            "heading": gps_state["heading"],
            "timestamp": gps_state["timestamp"], # Already ISO format UTC
        }
        publish_to_mqtt(MQTT_TOPIC_POSITION, payload, qos=1, retain=False)

def publish_gps_status():
    """Publishes GPS fix status and quality to MQTT_TOPIC_GPS_STATUS."""
    global gps_state, last_status_publish_time
    payload = {
        "has_fix": gps_state["has_fix"],
        "fix_quality": gps_state["fix_quality"],
        "num_satellites": gps_state["num_satellites"],
        "timestamp": get_utc_iso_timestamp() # Timestamp of the status update itself
    }
    # Publish status regardless of fix, retain the latest status
    publish_to_mqtt(MQTT_TOPIC_GPS_STATUS, payload, qos=1, retain=True)
    last_status_publish_time = time.time() # Record time of this publish

# --- End Publishing Functions ---


# --- Serial Port Handling (Revised: triggers status publish on change) ---

def read_from_serial():
    """Reads lines from serial port, processes NMEA, triggers publishes."""
    global serial_connection, gps_state, shutdown_flag, serial_read_error_count
    print("Serial reading thread started.")
    while not shutdown_flag.is_set():
        processed_line = False
        if serial_connection and serial_connection.is_open:
            try:
                if serial_connection.in_waiting > 0:
                    line = serial_connection.readline()
                    processed_line = True # We attempted to process something
                    if line:
                        if serial_read_error_count > 0: print("Serial communication resumed.")
                        serial_read_error_count = 0
                        try:
                            nmea_sentence = line.decode('utf-8', errors='ignore').strip()
                            if nmea_sentence.startswith('$'):
                                # update_from_nmea returns True if status fields changed
                                if update_from_nmea(nmea_sentence):
                                    # Publish status immediately if it changed
                                    publish_gps_status()

                                # Publish position and check laps only if we have a fix
                                if gps_state["has_fix"]:
                                    publish_position_data()
                                    update_lap_status()
                            # else: Ignore non-NMEA lines
                        except UnicodeDecodeError: gps_state["error_count"] += 1
                        except Exception as e: print(f"Error processing serial line: {e}"); gps_state["error_count"] += 1
                    else: # Readline returned empty data
                        serial_read_error_count += 1; time.sleep(0.1)
                else: # No data waiting
                    if serial_read_error_count > 0: serial_read_error_count = 0
                    # No data, sleep briefly before checking periodic publish in main loop
                    time.sleep(0.05)

                # Reconnect logic (unchanged)
                if serial_read_error_count >= MAX_SERIAL_READ_ERRORS_BEFORE_RECONNECT:
                    print(f"Max serial read errors ({serial_read_error_count}) reached. Reconnecting.")
                    close_serial(); time.sleep(0.1); open_serial(); serial_read_error_count = 0

            except (serial.SerialException, IOError) as e:
                print(f"Serial Exception/IO Error: {e}. Reconnecting.")
                close_serial(); time.sleep(0.1); open_serial(); serial_read_error_count = 0
            except Exception as e:
                print(f"Unexpected error in serial read loop: {e}"); serial_read_error_count += 1; time.sleep(0.1)
        else: # Serial port not open
            # Wait longer before retrying to open, main loop handles periodic status
            time.sleep(1.0)
            open_serial(); serial_read_error_count = 0

        # If we didn't process a line (e.g., no data waiting, port closed), yield CPU briefly
        if not processed_line:
            time.sleep(0.1) # Prevent busy-waiting when idle

    print("Serial reading thread finished.")

# open_serial and close_serial functions remain the same
def open_serial():
    """Opens the serial port connection."""
    global serial_connection
    if serial_connection and serial_connection.is_open: return True
    try:
        print(f"Attempting to open serial port {SERIAL_PORT} at {BAUD_RATE} baud...")
        serial_connection = serial.Serial(port=SERIAL_PORT, baudrate=BAUD_RATE,
                                          bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                                          stopbits=serial.STOPBITS_ONE, timeout=1)
        if serial_connection.is_open:
            print(f"Serial port {SERIAL_PORT} opened successfully.")
            serial_connection.reset_input_buffer(); serial_connection.reset_output_buffer()
            return True
        else:
            print(f"Failed to open serial port {SERIAL_PORT} (is_open is false).")
            serial_connection = None; return False
    except serial.SerialException as e:
        print(f"Error opening serial port {SERIAL_PORT}: {e}")
        serial_connection = None; return False
    except Exception as e:
        print(f"Unexpected error opening serial port: {e}")
        serial_connection = None; return False

def close_serial():
    """Closes the serial port connection."""
    global serial_connection
    if serial_connection and serial_connection.is_open:
        try:
            serial_connection.close(); print("Serial port closed.")
        except Exception as e: print(f"Error closing serial port: {e}")
    serial_connection = None
# --- End Serial Port Handling ---


# --- MQTT Setup (Unchanged) ---
def setup_mqtt():
    """Sets up and connects the MQTT client."""
    global mqtt_client
    try:
        try: # V2 API
            mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID, clean_session=True)
            print("Using paho-mqtt v2 API.")
            mqtt_client.on_disconnect = on_disconnect
            mqtt_client.on_publish = on_publish
        except AttributeError: # Fallback V1
            print("paho-mqtt v2 API not found, falling back to v1 compatible.")
            mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
            mqtt_client.on_disconnect = lambda c, u, rc: print(f"Disconnected (v1 API): rc={rc}")
            mqtt_client.on_publish = lambda c, u, mid: None # Suppress v1 logs

        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_message
        if MQTT_USERNAME and MQTT_PASSWORD: mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        lwt_payload = json.dumps({"status": "offline", "reason": "unexpected disconnect", "timestamp": get_utc_iso_timestamp()})
        mqtt_client.will_set(MQTT_TOPIC_GPS_STATUS, payload=lwt_payload, qos=1, retain=True)

        print(f"Attempting to connect to MQTT broker {MQTT_BROKER}:{MQTT_PORT}...")
        mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        return True
    except Exception as e: print(f"Error setting up MQTT: {e}"); return False
# --- End MQTT Setup ---


# --- Main Execution & Shutdown (Revised: Periodic Status Publish) ---
def signal_handler(signum, frame):
    print(f"\nSignal {signum} received, initiating shutdown...")
    shutdown_flag.set()

def main():
    global mqtt_client, last_status_publish_time

    print("Starting GPS Lap Monitor...")
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not open_serial(): print("Warning: Failed to open serial port on startup. Will retry.")
    if not setup_mqtt(): print("Critical: Failed to setup MQTT on startup. Exiting."); close_serial(); return 1

    serial_thread = threading.Thread(target=read_from_serial, name="SerialReader", daemon=True)
    serial_thread.start()

    status_publish_interval = 1.0 # Publish status at least every 1 second

    try:
        while not shutdown_flag.is_set():
            now = time.time()

            # --- Periodic GPS Status Publish ---
            # Publish status if enough time has passed since the last publish,
            # regardless of NMEA updates. Acts as a heartbeat.
            if (now - last_status_publish_time) >= status_publish_interval:
                # print(f"Debug: Periodic status publish check ({(now - last_status_publish_time):.1f}s elapsed)") # Debug
                publish_gps_status() # This also updates last_status_publish_time

            # --- Check Serial Thread Health ---
            if not serial_thread.is_alive():
                 print("Error: Serial reading thread died. Attempting restart...")
                 close_serial(); time.sleep(0.1)
                 if open_serial():
                     serial_thread = threading.Thread(target=read_from_serial, name="SerialReader", daemon=True)
                     serial_thread.start()
                     print("Serial thread restarted.")
                 else:
                     print("Error: Could not reopen serial port for thread restart. Shutting down.")
                     shutdown_flag.set() # Trigger shutdown if restart fails

            # Sleep for a short duration before next check cycle
            # Adjust sleep time based on desired responsiveness vs CPU usage
            # Sleep duration should be less than status_publish_interval
            time.sleep(0.5) # Check status/health twice per second

    except Exception as e:
        print(f"Unexpected error in main loop: {e}")
        shutdown_flag.set()
    finally:
        print("Shutting down...")
        if mqtt_client:
            print("Publishing final offline status...")
            try:
                final_status = {"status": "offline", "reason": "clean shutdown", "timestamp": get_utc_iso_timestamp()}
                publish_to_mqtt(MQTT_TOPIC_GPS_STATUS, final_status, qos=1, retain=True)
                time.sleep(0.5) # Allow time for publish
            except Exception as pub_e: print(f"Warning: Could not publish final status: {pub_e}")

            print("Stopping MQTT client...")
            try:
                mqtt_client.loop_stop(); mqtt_client.disconnect(); print("MQTT client disconnected.")
            except Exception as disc_e: print(f"Error during MQTT disconnect: {disc_e}")

        if serial_thread.is_alive():
            print("Waiting for serial thread to exit..."); serial_thread.join(timeout=3.0)
            if serial_thread.is_alive(): print("Warning: Serial thread did not exit cleanly.")

        close_serial()
        print("GPS Lap Monitor stopped.")
        sys.exit(0)

if __name__ == "__main__":
    main()
