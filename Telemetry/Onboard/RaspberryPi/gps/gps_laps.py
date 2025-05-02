import json
import time
import serial
import pynmea2
import paho.mqtt.client as mqtt
from datetime import datetime, timezone
import signal
import sys
import os
import logging

# --- Configuration ---

# Set up logging - Only to console (StreamHandler)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# MQTT Configuration
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_USERNAME = "eco"
MQTT_PASSWORD = "marathon" # Consider using environment variables or a config file

# --- MQTT Topics ---
# Publish Topics
MQTT_TOPIC_POSITION = "gps/position" # Current position data
MQTT_TOPIC_STATUS = "gps/status"     # GPS status data
MQTT_TOPIC_LAP_DATA = "gps/lap_data" # Completed lap data

# Subscribe Topics (Config - Expect Retained QoS 2 messages)
MQTT_TOPIC_CONFIG_START = "gps/config/start_line"
MQTT_TOPIC_CONFIG_FINISH = "gps/config/finish_line"
MQTT_TOPIC_CONFIG_LAP = "gps/config/lap_line" # Intermediate timing line

# Serial Port Configuration
SERIAL_PORT = '/dev/serial0' # Use the stable alias
SERIAL_BAUD = 115200
SERIAL_TIMEOUT = 0.5 # Read timeout

# Lap Timing Configuration
POSITION_PUBLISH_INTERVAL = 0.25 # Target ~4Hz for position updates
STATUS_PUBLISH_INTERVAL = 1.0   # Target 1Hz for status updates
LAP_PUBLISH_QOS = 1             # QoS for publishing lap data
CONFIG_SUBSCRIBE_QOS = 2        # QoS for receiving line definitions

# --- Global Variables ---
client = mqtt.Client()
ser = None
gps_data = None # Initialize later after defining class

# --- Line Crossing Logic ---

def side_of_line(A, B, P):
    """
    Returns the signed area (cross product) to determine which side of line AB point P is on.
    >0: Left side
    <0: Right side
    =0: On the line
    Assumes A, B, P are tuples (lon, lat)
    """
    try:
        # Ensure coordinates are floats for calculation
        Ax, Ay = float(A[0]), float(A[1])
        Bx, By = float(B[0]), float(B[1])
        Px, Py = float(P[0]), float(P[1])
        return (Bx - Ax) * (Py - Ay) - (By - Ay) * (Px - Ax)
    except (TypeError, IndexError, ValueError) as e:
        logging.error(f"Error calculating side_of_line (A={A}, B={B}, P={P}): {e}")
        return 0 # Treat as on the line in case of error

def directed_crossed(line_start, line_end, car_pos_prev, car_pos_curr):
    """
    Returns True if segment car_pos_prev -> car_pos_curr crosses
    the directed line line_start -> line_end.
    Detects a crossing from the 'right' side to the 'left' side or onto the line.
    """
    if not all([line_start, line_end, car_pos_prev, car_pos_curr]):
        # logging.debug("Cannot check crossing, missing point data.")
        return False # Cannot check if any point is missing

    s1 = side_of_line(line_start, line_end, car_pos_prev)
    s2 = side_of_line(line_start, line_end, car_pos_curr)

    # Crossing occurs if previous point was on the 'negative' side (right)
    # and current point is on the 'positive' side (left) or exactly on the line.
    return s1 < 0 and s2 >= 0

# --- GPS Data Class ---

class GPSData:
    """Holds and updates GPS state, including lap timing."""
    def __init__(self):
        # Position/Status Data
        self.position_data = {
            'timestamp': '', 'latitude': None, 'longitude': None,
            'altitude': None, 'speed': None, 'heading': None
        }
        self.status_data = {
            'status': 'initializing', 'satellites_used': 0, 'satellites_visible': 0,
            'hdop': None, 'fix_type': 'No Fix', 'last_fix_time': None,
            'uptime': 0, 'signal_quality': 'poor'
        }
        self.has_fix = False
        self.last_update = 0
        self.fix_lost_time = 0
        self.start_time = time.time()

        # Lap Timing Data
        self.start_line = None  # Tuple: ((lon1, lat1), (lon2, lat2))
        self.finish_line = None # Tuple: ((lon1, lat1), (lon2, lat2))
        self.lap_line = None    # Tuple: ((lon1, lat1), (lon2, lat2))
        self.last_position_coords = None # Tuple: (lon, lat)

        self.current_lap = 0
        self.lap_start_time = None # timestamp
        self.race_start_time = None # timestamp
        self.last_lap_time = None   # duration in seconds
        self.race_finished = False # Not currently used, could be set via MQTT

    def set_line(self, line_type, p1, p2):
        """Safely sets a line definition, converting coords to float."""
        try:
            coords = ((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))
            if line_type == 'start':
                self.start_line = coords
                logging.info(f"Start line configured: {coords}")
            elif line_type == 'finish':
                self.finish_line = coords
                logging.info(f"Finish line configured: {coords}")
            elif line_type == 'lap':
                self.lap_line = coords
                logging.info(f"Lap line configured: {coords}")
            else:
                logging.warning(f"Unknown line type: {line_type}")
        except (IndexError, ValueError, TypeError) as e:
            logging.error(f"Failed to parse/set {line_type} line coordinates (p1={p1}, p2={p2}): {e}")

    def _update_signal_quality(self):
        # (Same as before)
        if self.status_data['hdop'] is None: self.status_data['signal_quality'] = 'poor'
        elif self.status_data['hdop'] < 1.0: self.status_data['signal_quality'] = 'excellent'
        elif self.status_data['hdop'] < 2.0: self.status_data['signal_quality'] = 'good'
        elif self.status_data['hdop'] < 5.0: self.status_data['signal_quality'] = 'moderate'
        else: self.status_data['signal_quality'] = 'poor'

    def update_from_nmea(self, msg):
        """Updates position/status and checks for lap line crossings."""
        current_time = time.time()
        old_fix_status = self.has_fix
        self.status_data['uptime'] = int(current_time - self.start_time)
        position_updated = False
        current_coords = None

        try:
            # --- NMEA Parsing (largely same as before) ---
            if isinstance(msg, pynmea2.GGA):
                # Satellites Used, HDOP, Fix Quality, Altitude
                # ... (parsing logic as before) ...
                if hasattr(msg, 'num_sats') and msg.num_sats is not None: try: self.status_data['satellites_used'] = int(msg.num_sats)
                except (ValueError, TypeError): logging.warning(f"Could not parse GGA num_sats: {msg.num_sats}"); self.status_data['satellites_used'] = 0
                else: self.status_data['satellites_used'] = 0
                if hasattr(msg, 'horizontal_dil') and msg.horizontal_dil is not None: try: self.status_data['hdop'] = float(msg.horizontal_dil)
                except (ValueError, TypeError): logging.warning(f"Could not parse GGA HDOP: {msg.horizontal_dil}"); self.status_data['hdop'] = None
                else: self.status_data['hdop'] = None
                self._update_signal_quality()
                fix_quality = 0;
                if hasattr(msg, 'gps_qual') and msg.gps_qual is not None: try: fix_quality = int(msg.gps_qual)
                except (ValueError, TypeError): fix_quality = 0
                if fix_quality > 0:
                    self.has_fix = True; self.status_data['status'] = 'position'; self.status_data['last_fix_time'] = datetime.now(timezone.utc).isoformat()
                    if hasattr(msg, 'latitude') and msg.latitude is not None and hasattr(msg, 'longitude') and msg.longitude is not None:
                        try:
                            self.position_data['latitude'] = float(msg.latitude)
                            self.position_data['longitude'] = float(msg.longitude)
                            self.position_data['timestamp'] = datetime.now(timezone.utc).isoformat()
                            current_coords = (self.position_data['longitude'], self.position_data['latitude']) # Store LON, LAT
                            position_updated = True
                        except (ValueError, TypeError): logging.warning(f"Could not parse GGA lat/lon: {msg.latitude}/{msg.longitude}"); self.position_data['latitude']=None; self.position_data['longitude']=None; self.has_fix=False
                    else: self.has_fix = False
                    if hasattr(msg, 'altitude') and msg.altitude is not None: try: self.position_data['altitude'] = float(msg.altitude)
                    except (ValueError, TypeError): logging.warning(f"Could not parse GGA altitude: {msg.altitude}"); self.position_data['altitude'] = None
                    else: self.position_data['altitude'] = None
                else: self.has_fix = False; self.status_data['status'] = 'searching';
                if old_fix_status and not self.has_fix and not self.fix_lost_time: self.fix_lost_time = current_time

            elif isinstance(msg, pynmea2.RMC):
                # Speed, Heading, RMC Status Check
                # ... (parsing logic as before) ...
                rmc_status_active = hasattr(msg, 'status') and msg.status == 'A'
                speed_val = None; heading_val = None
                if rmc_status_active:
                    if hasattr(msg, 'spd_over_grnd') and msg.spd_over_grnd is not None: try: speed_val = float(msg.spd_over_grnd) * 1.852
                    except (ValueError, TypeError): logging.warning(f"Could not parse RMC speed: {msg.spd_over_grnd}")
                    if hasattr(msg, 'true_course') and msg.true_course is not None: try: heading_val = float(msg.true_course)
                    except (ValueError, TypeError): logging.warning(f"Could not parse RMC true_course: {msg.true_course}")
                    elif hasattr(msg, 'cog') and msg.cog is not None: try: heading_val = float(msg.cog)
                    except (ValueError, TypeError): logging.warning(f"Could not parse RMC cog: {msg.cog}")
                    # Update position from RMC if not already done by GGA and RMC has lat/lon
                    if not position_updated and hasattr(msg, 'latitude') and msg.latitude is not None and hasattr(msg, 'longitude') and msg.longitude is not None:
                         try:
                            self.position_data['latitude'] = float(msg.latitude)
                            self.position_data['longitude'] = float(msg.longitude)
                            self.position_data['timestamp'] = datetime.now(timezone.utc).isoformat()
                            current_coords = (self.position_data['longitude'], self.position_data['latitude']) # Store LON, LAT
                            position_updated = True
                            self.has_fix = True # RMC 'A' implies fix
                         except (ValueError, TypeError): logging.warning(f"Could not parse RMC lat/lon: {msg.latitude}/{msg.longitude}")

                self.position_data['speed'] = speed_val
                self.position_data['heading'] = heading_val
                if not rmc_status_active and self.has_fix: logging.info("RMC status is 'V', marking fix as lost."); self.has_fix = False; self.status_data['status'] = 'searching';
                if old_fix_status and not self.has_fix and not self.fix_lost_time: self.fix_lost_time = current_time

            elif isinstance(msg, pynmea2.GSA):
                # Fix Type, GSA HDOP fallback
                # ... (parsing logic as before) ...
                fix_type = 'No Fix';
                if hasattr(msg, 'mode_fix_type'):
                    if msg.mode_fix_type == '3': fix_type = '3D'
                    elif msg.mode_fix_type == '2': fix_type = '2D'
                self.status_data['fix_type'] = fix_type
                if self.status_data['hdop'] is None and hasattr(msg, 'hdop') and msg.hdop is not None: try: self.status_data['hdop'] = float(msg.hdop); self._update_signal_quality()
                except (ValueError, TypeError): logging.warning(f"Could not parse GSA HDOP: {msg.hdop}"); self.status_data['hdop'] = None; self._update_signal_quality()

            elif isinstance(msg, pynmea2.GSV):
                # Satellites Visible
                # ... (parsing logic as before) ...
                try:
                    if hasattr(msg, 'msg_num') and msg.msg_num is not None and hasattr(msg, 'num_sv_in_view') and msg.num_sv_in_view is not None:
                        if int(msg.msg_num) == 1: self.status_data['satellites_visible'] = int(msg.num_sv_in_view)
                except (ValueError, TypeError) as e: logging.warning(f"Error processing GSV message: {e} - Data: {msg}")

        except AttributeError as e: logging.warning(f"Attribute error parsing NMEA data: {e} - Sentence: {msg}")
        except Exception as e: logging.error(f"Error parsing NMEA data type {type(msg).__name__}: {e}")

        # --- Post-processing & Lap Timing ---
        if old_fix_status and not self.has_fix and not self.fix_lost_time: self.fix_lost_time = current_time
        if not self.has_fix and self.fix_lost_time and (current_time - self.fix_lost_time > 5):
            logging.info("Fix lost for > 5 seconds, clearing position data.")
            self.position_data['latitude'] = None; self.position_data['longitude'] = None; self.position_data['altitude'] = None
            self.position_data['speed'] = None; self.position_data['heading'] = None; self.position_data['timestamp'] = ''
            self.fix_lost_time = 0; self.last_position_coords = None # Clear last coords if fix lost
        if self.has_fix and self.fix_lost_time: logging.info("GPS fix regained."); self.fix_lost_time = 0

        # --- Line Crossing Checks ---
        # Only proceed if we have a valid current position and a previous position
        if position_updated and current_coords and self.last_position_coords:
            # Check Start Line Crossing
            if self.start_line and directed_crossed(self.start_line[0], self.start_line[1], self.last_position_coords, current_coords):
                if self.race_start_time is None:
                    self.race_start_time = current_time
                    self.lap_start_time = current_time
                    self.current_lap = 1
                    logging.info(f"*** Race Started! Lap {self.current_lap} begins. ***")
                    # Optionally publish a "race start" event here
                else:
                    # This case (crossing start line again) might indicate an out-lap or error
                    # For now, we only care about the *first* crossing to start the race.
                    # If using start line *also* as finish line, logic needs adjustment.
                    logging.debug("Crossed start line again (ignored for race start).")

            # Check Finish Line Crossing (Completes a lap)
            # Ensure race has started and finish line is defined
            if self.finish_line and self.race_start_time is not None and self.lap_start_time is not None and \
               directed_crossed(self.finish_line[0], self.finish_line[1], self.last_position_coords, current_coords):

                lap_time_sec = current_time - self.lap_start_time
                total_time_sec = current_time - self.race_start_time
                self.last_lap_time = lap_time_sec

                logging.info(f"*** Lap {self.current_lap} Completed! Time: {lap_time_sec:.3f}s ***")

                # Prepare lap data payload
                lap_payload = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "lap_number": self.current_lap,
                    "lap_time_sec": round(lap_time_sec, 3),
                    "total_time_sec": round(total_time_sec, 3)
                }
                # Publish lap data
                try:
                    result, mid = client.publish(MQTT_TOPIC_LAP_DATA, json.dumps(lap_payload), qos=LAP_PUBLISH_QOS)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        logging.warning(f"Failed to publish lap data (Result code: {result})")
                    else:
                        logging.info(f"Published Lap Data: {lap_payload}")
                except Exception as e:
                    logging.error(f"Error publishing lap data: {e}")

                # Start next lap
                self.current_lap += 1
                self.lap_start_time = current_time
                logging.info(f"--- Lap {self.current_lap} begins. ---")

            # Check Intermediate Lap Line Crossing
            if self.lap_line and self.race_start_time is not None and \
               directed_crossed(self.lap_line[0], self.lap_line[1], self.last_position_coords, current_coords):
                intermediate_time = current_time - (self.lap_start_time if self.lap_start_time else self.race_start_time)
                logging.info(f"--- Crossed intermediate lap line at lap time: {intermediate_time:.3f}s (Lap {self.current_lap}) ---")
                # Optionally publish intermediate timing data here

        # Update last known position if we got a valid one this cycle
        if position_updated and current_coords:
            self.last_position_coords = current_coords

        self.last_update = current_time

    def get_position_json(self):
        # (Same as before)
        if self.position_data['latitude'] is not None and self.position_data['longitude'] is not None:
            position_copy = {k: v for k, v in self.position_data.items() if v is not None and v != ''}
            if 'timestamp' not in position_copy or not position_copy['timestamp']: position_copy['timestamp'] = datetime.now(timezone.utc).isoformat()
            return json.dumps(position_copy)
        return None

    def get_status_json(self):
        # (Same as before)
        status_copy = {k: v for k, v in self.status_data.items() if v is not None}
        if 'uptime' not in status_copy: status_copy['uptime'] = int(time.time() - self.start_time)
        # Add lap info to status
        status_copy['current_lap'] = self.current_lap
        status_copy['last_lap_time_sec'] = round(self.last_lap_time, 3) if self.last_lap_time is not None else None
        return json.dumps(status_copy)

# --- MQTT Callbacks ---

def on_connect(client, userdata, flags, rc):
    """Callback when MQTT connection is established."""
    if rc == 0:
        logging.info(f"Connected to MQTT broker with result code {rc}")
        # Subscribe to configuration topics upon connection
        try:
            # Use QoS 2 for config topics as requested
            client.subscribe([(MQTT_TOPIC_CONFIG_START, CONFIG_SUBSCRIBE_QOS),
                              (MQTT_TOPIC_CONFIG_FINISH, CONFIG_SUBSCRIBE_QOS),
                              (MQTT_TOPIC_CONFIG_LAP, CONFIG_SUBSCRIBE_QOS)])
            logging.info(f"Subscribed to config topics: {MQTT_TOPIC_CONFIG_START}, {MQTT_TOPIC_CONFIG_FINISH}, {MQTT_TOPIC_CONFIG_LAP}")
        except Exception as e:
            logging.error(f"Error subscribing to config topics: {e}")
    else:
        logging.error(f"Failed to connect to MQTT broker, return code {rc}")

def on_disconnect(client, userdata, rc):
    """Callback when MQTT disconnects."""
    logging.warning(f"Disconnected from MQTT broker with result code {rc}. Will attempt reconnection.")

def on_message(client, userdata, msg):
    """Callback when a message is received (used for line config)."""
    global gps_data
    topic = msg.topic
    try:
        payload_str = msg.payload.decode('utf-8')
        logging.info(f"Received config message on topic '{topic}'")
        # logging.debug(f"Payload: {payload_str}") # Uncomment for debugging payload
        data = json.loads(payload_str)

        if 'p1' not in data or 'p2' not in data or len(data['p1']) != 2 or len(data['p2']) != 2:
            logging.error(f"Invalid coordinate format in message on {topic}: {payload_str}")
            return

        p1_coords = data['p1'] # Expected: [lon, lat]
        p2_coords = data['p2'] # Expected: [lon, lat]

        if gps_data is None:
             logging.warning("GPSData object not initialized yet, cannot set line.")
             return

        if topic == MQTT_TOPIC_CONFIG_START:
            gps_data.set_line('start', p1_coords, p2_coords)
        elif topic == MQTT_TOPIC_CONFIG_FINISH:
            gps_data.set_line('finish', p1_coords, p2_coords)
        elif topic == MQTT_TOPIC_CONFIG_LAP:
            gps_data.set_line('lap', p1_coords, p2_coords)
        else:
            logging.warning(f"Received message on unexpected topic: {topic}")

    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON payload on topic {topic}: {msg.payload.decode('utf-8', errors='replace')}")
    except UnicodeDecodeError:
        logging.error(f"Failed to decode payload (not UTF-8?) on topic {topic}")
    except Exception as e:
        logging.error(f"Error processing message on topic {topic}: {e}", exc_info=True)


# Assign callbacks
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# --- Signal Handling ---

def signal_handler(sig, frame):
    """Handle termination signals gracefully."""
    logging.info("Received termination signal. Cleaning up...")
    global ser
    if ser is not None and ser.is_open:
        logging.info("Closing serial connection...")
        try: ser.close()
        except Exception as e: logging.error(f"Error closing serial port during cleanup: {e}")
    if client.is_connected():
        logging.info("Disconnecting from MQTT broker...")
        client.loop_stop()
        client.disconnect()
    logging.info("Cleanup complete. Exiting.")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Connection Functions ---

def connect_mqtt():
    """Establishes connection to the MQTT broker."""
    try:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start() # Start background network loop
        # on_connect callback handles logging success/failure and subscriptions
        return True # Assume connection attempt initiated
    except Exception as e:
        logging.error(f"Failed to initiate MQTT connection: {e}")
        client.loop_stop() # Ensure loop isn't running if connect fails immediately
        return False

def connect_serial():
    """Establishes connection to the serial port."""
    global ser
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
        logging.info(f"Connected to GPS serial port {SERIAL_PORT}")
        return ser
    except serial.SerialException as e:
        logging.error(f"Failed to open serial port {SERIAL_PORT}: {e}")
        if "Permission denied" in str(e): logging.error("Hint: Ensure user is in 'dialout' group and re-login/reboot.")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred opening serial port {SERIAL_PORT}: {e}")
        return None

# --- Main Loop ---

def main():
    global gps_data
    gps_data = GPSData() # Initialize the data object
    logging.info("Starting GPS monitoring script with Lap Timing...")

    last_status_publish_time = 0
    last_position_publish_time = 0
    reconnect_delay = 2 # Initial delay
    max_reconnect_delay = 60

    while True:
        try:
            # --- Connection Management ---
            mqtt_connected = client.is_connected()
            serial_connected = ser is not None and ser.is_open

            if not mqtt_connected:
                logging.info("MQTT disconnected. Attempting to reconnect...")
                if connect_mqtt():
                     time.sleep(2) # Give time for connection/callbacks
                     if client.is_connected():
                          reconnect_delay = 2 # Reset delay on success
                     else:
                          logging.warning(f"MQTT connection attempt failed. Retrying in {reconnect_delay} seconds.")
                          time.sleep(reconnect_delay)
                          reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                          continue
                else: # connect_mqtt failed immediately
                     logging.warning(f"MQTT connection initiation failed. Retrying in {reconnect_delay} seconds.")
                     time.sleep(reconnect_delay)
                     reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                     continue

            if not serial_connected:
                 logging.info("Serial port not connected. Attempting to connect...")
                 ser = connect_serial()
                 if not ser:
                    logging.warning(f"Serial connection failed. Retrying in {reconnect_delay} seconds.")
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue
                 else:
                    reconnect_delay = 2 # Reset delay

            # --- Inner loop for reading serial data ---
            # logging.info("Connections established. Starting serial read loop.") # Too verbose
            consecutive_error_count = 0
            max_consecutive_errors = 10

            while client.is_connected() and ser is not None and ser.is_open:
                current_time = time.time()

                try:
                    bytes_waiting = ser.in_waiting
                    if bytes_waiting > 0:
                        line = ser.readline()
                        if not line: continue # Skip empty reads

                        try:
                            line_str = line.decode('ascii', errors='replace').strip()
                            if not line_str or not line_str.startswith('$'): continue # Skip non-NMEA

                            msg = pynmea2.parse(line_str)
                            gps_data.update_from_nmea(msg) # Update state & check laps
                            consecutive_error_count = 0 # Reset errors on success

                            # --- Publishing Logic ---
                            # Position (if fix & interval passed)
                            if gps_data.has_fix and (current_time - last_position_publish_time >= POSITION_PUBLISH_INTERVAL):
                                position_json = gps_data.get_position_json()
                                if position_json:
                                    result, mid = client.publish(MQTT_TOPIC_POSITION, position_json, qos=1)
                                    if result != mqtt.MQTT_ERR_SUCCESS: logging.warning(f"Failed to publish position (Code: {result})")
                                    # logging.debug(f"Published Position: {position_json}")
                                last_position_publish_time = current_time

                            # Status (if interval passed)
                            if current_time - last_status_publish_time >= STATUS_PUBLISH_INTERVAL:
                                status_json = gps_data.get_status_json()
                                result, mid = client.publish(MQTT_TOPIC_STATUS, status_json, qos=0)
                                if result != mqtt.MQTT_ERR_SUCCESS: logging.warning(f"Failed to publish status (Code: {result})")
                                logging.info(f"Status: {status_json}") # Log status periodically
                                last_status_publish_time = current_time

                        except pynmea2.ParseError as e: logging.warning(f"NMEA Parse Error: {e} on line: {line_str}")
                        except UnicodeDecodeError as e: logging.warning(f"Unicode Decode Error: {e} on raw line: {line!r}")
                        except Exception as e: logging.error(f"Error processing NMEA line: {e}", exc_info=True) # Log other processing errors

                    else: # No data waiting
                        time.sleep(0.01) # Prevent high CPU usage

                except serial.SerialException as e:
                    logging.error(f"Serial error during read/check: {e}")
                    try: ser.close()
                    except Exception: pass
                    ser = None; break # Exit inner loop -> reconnect serial
                except OSError as e:
                     logging.error(f"OS error during serial operation: {e}")
                     consecutive_error_count += 1
                     if consecutive_error_count > max_consecutive_errors:
                          logging.warning(f"Too many consecutive OS errors ({consecutive_error_count}), breaking inner loop.")
                          try: ser.close()
                          except Exception: pass
                          ser = None; break # Exit inner loop -> reconnect serial
                     time.sleep(0.5)
                except Exception as e:
                    logging.error(f"Unexpected error in inner loop: {e}", exc_info=True)
                    consecutive_error_count += 1
                    if consecutive_error_count > max_consecutive_errors:
                        logging.warning(f"Too many consecutive errors ({consecutive_error_count}), breaking inner loop.")
                        try: ser.close()
                        except Exception: pass
                        ser = None; break # Exit inner loop -> reconnect serial
                    time.sleep(0.5)
            # --- End of inner loop ---
            # logging.info("Exited serial read loop.") # Too verbose

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt received, initiating shutdown.")
            break # Exit main loop, signal handler cleans up

        except Exception as e:
            logging.error(f"Fatal error in main loop: {e}", exc_info=True)
            # Attempt cleanup before retry
            try:
                if ser is not None and ser.is_open: ser.close()
            except Exception: pass
            ser = None
            try:
                if client.is_connected(): client.loop_stop(); client.disconnect()
            except Exception: pass
            logging.info(f"Retrying after main loop error in {reconnect_delay} seconds.")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    # Cleanup if loop exited other than via signal handler (shouldn't happen often)
    signal_handler(None, None)

if __name__ == "__main__":
    main()