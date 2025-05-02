import json
import time
import serial
import pynmea2
import paho.mqtt.client as mqtt
from datetime import datetime
import signal
import sys
import os
import logging # Keep logging import for console output

# Set up logging - Only to console (StreamHandler)
logging.basicConfig(
    level=logging.INFO, # INFO level provides good operational details
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # Removed: logging.FileHandler("gps_monitor.log"),
        logging.StreamHandler() # Log messages to the console (stderr by default)
    ]
)

# MQTT Configuration
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_TOPIC_POSITION = "gps/position"
MQTT_TOPIC_STATUS = "gps/status"
MQTT_USERNAME = "eco"
MQTT_PASSWORD = "marathon" # Consider using environment variables or a config file for credentials

# Initialize MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Global serial connection for signal handler access
ser = None

def signal_handler(sig, frame):
    """Handle Ctrl+C and other termination signals gracefully"""
    logging.info("Received termination signal. Cleaning up...")

    # Close serial connection if open
    global ser
    if ser is not None and ser.is_open:
        logging.info("Closing serial connection...")
        try:
            ser.close()
        except Exception as e:
            logging.error(f"Error closing serial port during cleanup: {e}")

    # Disconnect MQTT if connected
    if client.is_connected():
        logging.info("Disconnecting from MQTT broker...")
        client.loop_stop() # Stop the network loop
        client.disconnect()

    logging.info("Cleanup complete. Exiting.")
    sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)  # Handles Ctrl+C
signal.signal(signal.SIGTERM, signal_handler) # Handles termination signals (e.g., from systemd)

def connect_mqtt():
    """Establishes connection to the MQTT broker."""
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60) # 60-second keepalive
        client.loop_start() # Start background network loop
        logging.info(f"Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to MQTT broker: {e}")
        return False

def connect_serial():
    """Establishes connection to the serial port."""
    global ser
    port_name = '/dev/ttyS0' # Or make this configurable
    baud_rate = 115200
    timeout_sec = 0.5 # Read timeout

    try:
        ser = serial.Serial(port_name, baud_rate, timeout=timeout_sec)
        logging.info(f"Connected to GPS serial port {port_name}")
        return ser
    except serial.SerialException as e:
        logging.error(f"Failed to open serial port {port_name}: {e}")
        # Common reasons: Permission denied (add user to 'dialout' group?), device not found, device busy
        if "Permission denied" in str(e):
             logging.error("Hint: Ensure the user running the script is in the 'dialout' group (e.g., sudo usermod -a -G dialout $USER) and reboot/re-login.")
        return None
    except Exception as e: # Catch other potential errors
        logging.error(f"An unexpected error occurred opening serial port {port_name}: {e}")
        return None


class GPSData:
    """Holds and updates GPS state based on NMEA sentences."""
    def __init__(self):
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

    def _update_signal_quality(self):
        """Updates signal quality based on HDOP."""
        if self.status_data['hdop'] is None:
            self.status_data['signal_quality'] = 'poor'
        elif self.status_data['hdop'] < 1.0:
            self.status_data['signal_quality'] = 'excellent'
        elif self.status_data['hdop'] < 2.0:
            self.status_data['signal_quality'] = 'good'
        elif self.status_data['hdop'] < 5.0:
            self.status_data['signal_quality'] = 'moderate'
        else:
            self.status_data['signal_quality'] = 'poor'

    def update_from_nmea(self, msg):
        """Updates position and status data from a parsed NMEA message."""
        current_time = time.time()
        old_fix_status = self.has_fix
        self.status_data['uptime'] = int(current_time - self.start_time) # Update uptime regardless of message type

        try:
            if isinstance(msg, pynmea2.GGA):
                # Satellites Used
                if hasattr(msg, 'num_sats') and msg.num_sats is not None:
                    try:
                        self.status_data['satellites_used'] = int(msg.num_sats)
                    except (ValueError, TypeError):
                        logging.warning(f"Could not parse GGA num_sats: {msg.num_sats}")
                        self.status_data['satellites_used'] = 0
                else:
                    self.status_data['satellites_used'] = 0

                # HDOP
                if hasattr(msg, 'horizontal_dil') and msg.horizontal_dil is not None:
                    try:
                        self.status_data['hdop'] = float(msg.horizontal_dil)
                    except (ValueError, TypeError):
                        logging.warning(f"Could not parse GGA HDOP: {msg.horizontal_dil}")
                        self.status_data['hdop'] = None
                else:
                    self.status_data['hdop'] = None # Reset HDOP if not present
                self._update_signal_quality() # Update quality based on HDOP

                # Fix Status & Position
                fix_quality = 0
                if hasattr(msg, 'gps_qual') and msg.gps_qual is not None:
                     try:
                          fix_quality = int(msg.gps_qual)
                     except (ValueError, TypeError):
                          fix_quality = 0

                if fix_quality > 0:
                    self.has_fix = True
                    self.status_data['status'] = 'position'
                    self.status_data['last_fix_time'] = datetime.utcnow().isoformat() + 'Z'

                    # Latitude / Longitude (essential for position)
                    if hasattr(msg, 'latitude') and msg.latitude is not None and \
                       hasattr(msg, 'longitude') and msg.longitude is not None:
                        try:
                            self.position_data['latitude'] = float(msg.latitude)
                            self.position_data['longitude'] = float(msg.longitude)
                            # Use UTC time from system, NMEA time can be unreliable until fix
                            self.position_data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
                        except (ValueError, TypeError):
                             logging.warning(f"Could not parse GGA lat/lon: {msg.latitude}/{msg.longitude}")
                             self.position_data['latitude'] = None
                             self.position_data['longitude'] = None
                             self.has_fix = False # Can't have a fix without lat/lon
                    else:
                         self.has_fix = False # Can't have a fix without lat/lon

                    # Altitude
                    if hasattr(msg, 'altitude') and msg.altitude is not None:
                        try:
                            self.position_data['altitude'] = float(msg.altitude)
                        except (ValueError, TypeError):
                            logging.warning(f"Could not parse GGA altitude: {msg.altitude}")
                            self.position_data['altitude'] = None
                    else:
                        self.position_data['altitude'] = None # Explicitly clear if not present
                else:
                    # No fix based on GGA quality indicator
                    self.has_fix = False
                    self.status_data['status'] = 'searching'
                    if old_fix_status and not self.fix_lost_time: # Record when fix was first lost
                        self.fix_lost_time = current_time

            elif isinstance(msg, pynmea2.RMC):
                 # RMC provides speed and heading. Also confirms fix status ('A').
                 rmc_status_active = hasattr(msg, 'status') and msg.status == 'A'

                 # Speed
                 speed_val = None
                 if rmc_status_active and hasattr(msg, 'spd_over_grnd') and msg.spd_over_grnd is not None:
                     try:
                         # Convert knots to km/h
                         speed_val = float(msg.spd_over_grnd) * 1.852
                     except (ValueError, TypeError):
                          logging.warning(f"Could not parse RMC speed: {msg.spd_over_grnd}")
                 self.position_data['speed'] = speed_val # Assign found value or None

                 # Heading - Prefer true_course, fallback to cog
                 heading_val = None
                 if rmc_status_active:
                     if hasattr(msg, 'true_course') and msg.true_course is not None:
                          try:
                              heading_val = float(msg.true_course)
                          except (ValueError, TypeError):
                              logging.warning(f"Could not parse RMC true_course: {msg.true_course}")
                     # Check hasattr before accessing cog
                     elif hasattr(msg, 'cog') and msg.cog is not None: # Check cog only if true_course failed/missing
                          try:
                              heading_val = float(msg.cog)
                              # logging.debug(f"Using COG ({heading_val}) as heading.") # Optional debug log
                          except (ValueError, TypeError):
                              logging.warning(f"Could not parse RMC cog: {msg.cog}")
                 self.position_data['heading'] = heading_val # Assign found value or None

                 # RMC status 'V' (Void) implies no fix, update status if GGA hasn't already
                 if not rmc_status_active and self.has_fix:
                      logging.info("RMC status is 'V', marking fix as lost.")
                      self.has_fix = False
                      self.status_data['status'] = 'searching'
                      if old_fix_status and not self.fix_lost_time:
                           self.fix_lost_time = current_time


            elif isinstance(msg, pynmea2.GSA):
                # Fix Type (2D/3D)
                fix_type = 'No Fix'
                if hasattr(msg, 'mode_fix_type'):
                    if msg.mode_fix_type == '3':
                        fix_type = '3D'
                    elif msg.mode_fix_type == '2':
                        fix_type = '2D'
                self.status_data['fix_type'] = fix_type

                # GSA HDOP (use as fallback if GGA didn't provide it)
                if self.status_data['hdop'] is None and hasattr(msg, 'hdop') and msg.hdop is not None:
                    try:
                        self.status_data['hdop'] = float(msg.hdop)
                        self._update_signal_quality()
                    except (ValueError, TypeError):
                         logging.warning(f"Could not parse GSA HDOP: {msg.hdop}")
                         self.status_data['hdop'] = None
                         self._update_signal_quality()


            elif isinstance(msg, pynmea2.GSV):
                # Satellites Visible (only use first message in sequence)
                try:
                    if hasattr(msg, 'msg_num') and msg.msg_num is not None and \
                       hasattr(msg, 'num_sv_in_view') and msg.num_sv_in_view is not None:
                        msg_num = int(msg.msg_num)
                        if msg_num == 1: # Only update on the first message
                            self.status_data['satellites_visible'] = int(msg.num_sv_in_view)
                except (ValueError, TypeError) as e:
                    logging.warning(f"Error processing GSV message: {e} - Data: {msg}")

        except AttributeError as e:
             # This can happen if pynmea2 encounters unexpected field variations
             logging.warning(f"Attribute error parsing NMEA data: {e} - Sentence: {msg}")
        except Exception as e:
            # Catch-all for other parsing issues
            logging.error(f"Error parsing NMEA data type {type(msg).__name__}: {e}")

        # --- Post-processing after parsing a message ---

        # If fix status changed from True to False, record the time
        if old_fix_status and not self.has_fix and not self.fix_lost_time:
             self.fix_lost_time = current_time

        # If we've lost fix for more than 5 seconds, clear potentially stale position data
        if not self.has_fix and self.fix_lost_time and (current_time - self.fix_lost_time > 5):
            logging.info("Fix lost for > 5 seconds, clearing position data.")
            self.position_data['latitude'] = None
            self.position_data['longitude'] = None
            self.position_data['altitude'] = None
            self.position_data['speed'] = None
            self.position_data['heading'] = None
            self.position_data['timestamp'] = '' # Clear timestamp too
            self.fix_lost_time = 0 # Reset timer after clearing

        # If we regained fix, reset the fix lost timer
        if self.has_fix and self.fix_lost_time:
            logging.info("GPS fix regained.")
            self.fix_lost_time = 0

        self.last_update = current_time

    def get_position_json(self):
        """Returns position data as JSON string, only if lat/lon are valid."""
        # Essential check: must have lat and lon for a valid position message
        if self.position_data['latitude'] is not None and self.position_data['longitude'] is not None:
            # Create a copy containing only non-None values for cleaner JSON
            position_copy = {k: v for k, v in self.position_data.items() if v is not None and v != ''}
            # Ensure timestamp is always present if we have lat/lon
            if 'timestamp' not in position_copy:
                 position_copy['timestamp'] = datetime.utcnow().isoformat() + 'Z'
            return json.dumps(position_copy)
        return None # Return None if no valid lat/lon

    def get_status_json(self):
        """Returns status data as JSON string."""
        # Create a copy containing only non-None values
        status_copy = {k: v for k, v in self.status_data.items() if v is not None}
        # Ensure uptime is always present
        if 'uptime' not in status_copy:
            status_copy['uptime'] = int(time.time() - self.start_time)
        return json.dumps(status_copy)

def main():
    logging.info("Starting GPS monitoring script...")
    gps_data = GPSData()
    last_status_publish_time = 0
    last_position_publish_time = 0
    reconnect_delay = 1 # Initial delay in seconds
    max_reconnect_delay = 60 # Maximum delay

    position_publish_interval = 0.25 # Target ~4Hz
    status_publish_interval = 1.0   # Target 1Hz

    global ser # Allow modification of the global serial object

    while True:
        try:
            # --- Connection Management ---

            # 1. Ensure MQTT is connected
            if not client.is_connected():
                logging.info("MQTT disconnected. Attempting to reconnect...")
                if not connect_mqtt():
                    logging.warning(f"MQTT reconnection failed. Retrying in {reconnect_delay} seconds.")
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue # Retry connection phase
                else:
                    reconnect_delay = 1 # Reset delay on successful MQTT connection

            # 2. Ensure Serial Port is connected
            if ser is None or not ser.is_open:
                 logging.info("Serial port not connected. Attempting to connect...")
                 ser = connect_serial()
                 if not ser:
                    logging.warning(f"Serial connection failed. Retrying in {reconnect_delay} seconds.")
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue # Retry connection phase
                 else:
                    reconnect_delay = 1 # Reset delay on successful serial connection


            # --- Inner loop for reading serial data ---
            logging.info("Connections established. Starting serial read loop.")
            consecutive_error_count = 0
            max_consecutive_errors = 10 # How many errors before breaking inner loop

            while True: # Keep reading from serial as long as it's open and MQTT is connected
                current_time = time.time()

                # Check MQTT connection periodically within the inner loop
                if not client.is_connected():
                     logging.warning("MQTT connection lost during serial read loop. Breaking to reconnect.")
                     break # Exit inner loop to trigger MQTT reconnect in outer loop

                try:
                    # Check if data is available using in_waiting
                    # This can raise OSError: [Errno 5] if the device disconnects abruptly
                    bytes_waiting = ser.in_waiting

                    if bytes_waiting > 0:
                        line = ser.readline()
                        if not line: # Should not happen if in_waiting > 0, but safety check
                             logging.warning("Readline returned empty despite data waiting.")
                             time.sleep(0.05) # Small pause
                             continue

                        try:
                            # Decode with error replacement for robustness
                            line_str = line.decode('ascii', errors='replace').strip()

                            # Skip empty lines or lines not starting with NMEA '$'
                            if not line_str or not line_str.startswith('$'):
                                # logging.debug(f"Skipping non-NMEA line: {line_str}")
                                continue

                            # Parse the NMEA sentence
                            msg = pynmea2.parse(line_str)

                            # Update GPS data state
                            gps_data.update_from_nmea(msg)

                            # Reset error count on successful parse
                            consecutive_error_count = 0

                            # --- Publishing Logic ---

                            # Publish Position data at target interval if fix is available
                            if gps_data.has_fix and (current_time - last_position_publish_time >= position_publish_interval):
                                position_json = gps_data.get_position_json()
                                if position_json: # Ensure we got valid JSON
                                    result, mid = client.publish(MQTT_TOPIC_POSITION, position_json, qos=1) # Use QoS 1 for position
                                    if result != mqtt.MQTT_ERR_SUCCESS:
                                        logging.warning(f"Failed to publish position data (Result code: {result})")
                                    # Optional: Log published position less frequently
                                    # logging.debug(f"Published Position: {position_json}")
                                last_position_publish_time = current_time

                            # Publish Status data at target interval (always)
                            if current_time - last_status_publish_time >= status_publish_interval:
                                status_json = gps_data.get_status_json()
                                result, mid = client.publish(MQTT_TOPIC_STATUS, status_json, qos=0) # Use QoS 0 for status
                                if result != mqtt.MQTT_ERR_SUCCESS:
                                     logging.warning(f"Failed to publish status data (Result code: {result})")
                                logging.info(f"Status: {status_json}") # Log status periodically
                                last_status_publish_time = current_time

                        except pynmea2.ParseError as e:
                            logging.warning(f"NMEA Parse Error: {e} on line: {line_str}")
                            continue # Skip this line, try the next one
                        except UnicodeDecodeError as e:
                            logging.warning(f"Unicode Decode Error: {e} on raw line: {line!r}")
                            continue # Skip this line

                    else:
                        # No data waiting, sleep briefly to prevent high CPU usage
                        time.sleep(0.01)

                except serial.SerialException as e:
                    logging.error(f"Serial error during read/check: {e}")
                    # This often indicates disconnection or a more serious issue
                    try: # Attempt to close the faulty port
                        if ser and ser.is_open:
                            ser.close()
                    except Exception as close_err:
                         logging.error(f"Error closing serial port after read error: {close_err}")
                    ser = None # Mark serial as disconnected
                    break # Exit inner loop to trigger serial reconnect in outer loop

                except OSError as e:
                     # Catch specific OS errors like [Errno 5] Input/output error from in_waiting
                     logging.error(f"OS error during serial operation: {e}")
                     consecutive_error_count += 1
                     if consecutive_error_count > max_consecutive_errors:
                          logging.warning(f"Too many consecutive OS errors ({consecutive_error_count}), breaking inner loop.")
                          try:
                              if ser and ser.is_open: ser.close()
                          except Exception as close_err:
                               logging.error(f"Error closing serial port after OS error: {close_err}")
                          ser = None
                          break # Exit inner loop
                     time.sleep(0.5) # Pause after an OS error before retrying

                except Exception as e:
                    # Catch any other unexpected errors in the inner loop
                    logging.error(f"Unexpected error in inner loop: {e}", exc_info=True) # Log traceback
                    consecutive_error_count += 1
                    if consecutive_error_count > max_consecutive_errors:
                        logging.warning(f"Too many consecutive errors ({consecutive_error_count}), breaking inner loop.")
                        try: # Attempt to close port on repeated errors
                            if ser and ser.is_open: ser.close()
                        except Exception as close_err:
                             logging.error(f"Error closing serial port after general error: {close_err}")
                        ser = None
                        break # Exit inner loop
                    time.sleep(0.5) # Short pause after an unexpected error
            # --- End of inner loop ---
            logging.info("Exited serial read loop.")

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt received, initiating shutdown.")
            # The signal handler (registered earlier) will perform cleanup.
            break # Exit the main while loop

        except Exception as e:
            # Catch errors in the outer loop (connection setup, unexpected issues)
            logging.error(f"Fatal error in main loop: {e}", exc_info=True)
            # Ensure connections are reset before retrying
            try:
                if ser is not None and ser.is_open:
                    ser.close()
            except Exception as close_err:
                 logging.error(f"Error closing serial port after main loop error: {close_err}")
            ser = None
            try:
                if client.is_connected():
                    client.loop_stop()
                    client.disconnect()
            except Exception as mqtt_err:
                 logging.error(f"Error disconnecting MQTT after main loop error: {mqtt_err}")

            logging.info(f"Retrying after main loop error in {reconnect_delay} seconds.")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    logging.info("GPS monitoring script finished.")
    # Cleanup is handled by the signal_handler upon exit

if __name__ == "__main__":
    main()
