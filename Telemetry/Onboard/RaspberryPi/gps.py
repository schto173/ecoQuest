import json
import time
import serial
import pynmea2
import paho.mqtt.client as mqtt
from datetime import datetime
import signal
import sys
import os
import gc  # Garbage collection
import logging

# Set up logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("gps_monitor.log"),
        logging.StreamHandler()
    ]
)

# MQTT Configuration
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_TOPIC_POSITION = "gps/position"
MQTT_TOPIC_STATUS = "gps/status"
MQTT_USERNAME = "eco"
MQTT_PASSWORD = "marathon"

# Initialize MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Global serial connection for signal handler access
ser = None

def signal_handler(sig, frame):
    """Handle Ctrl+C and other termination signals"""
    logging.info("Received termination signal. Cleaning up...")

    # Close serial connection if open
    global ser
    if ser is not None and ser.is_open:
        logging.info("Closing serial connection...")
        ser.close()

    # Disconnect MQTT if connected
    if client.is_connected():
        logging.info("Disconnecting from MQTT broker...")
        client.loop_stop()
        client.disconnect()

    logging.info("Cleanup complete. Exiting.")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Termination

def connect_mqtt():
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        logging.info("Connected to MQTT broker")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to MQTT broker: {e}")
        return False

def connect_serial():
    global ser

    # First, check if the port might be locked by a previous instance
    try:
        # Try to forcibly release the port if it's locked
        os.system(f"fuser -k /dev/ttyS0 2>/dev/null")
        time.sleep(1)  # Give the system time to release the port
    except:
        pass

    try:
        ser = serial.Serial('/dev/ttyS0', 115200, timeout=0.2)
        logging.info("Connected to GPS serial port")
        return ser
    except Exception as e:
        logging.error(f"Failed to open serial port: {e}")
        return None

class GPSData:
    def __init__(self):
        # Position data
        self.position_data = {
            'timestamp': '',
            'latitude': None,
            'longitude': None,
            'altitude': None,
            'speed': None,
            'heading': None
        }

        # Status data
        self.status_data = {
            'status': 'searching',
            'satellites_used': 0,
            'satellites_visible': 0,
            'hdop': None,
            'fix_type': 'No Fix',
            'last_fix_time': None,
            'uptime': 0,
            'signal_quality': 'poor'
        }

        self.has_fix = False
        self.last_update = 0
        self.fix_lost_time = 0
        self.start_time = time.time()

    def update_from_nmea(self, msg):
        current_time = time.time()
        old_fix_status = self.has_fix

        try:
            if isinstance(msg, pynmea2.GGA):
                # Update status data
                self.status_data['satellites_used'] = int(msg.num_sats) if msg.num_sats else 0
                self.status_data['uptime'] = int(current_time - self.start_time)

                if msg.horizontal_dil:
                    self.status_data['hdop'] = float(msg.horizontal_dil)
                    # Update signal quality based on HDOP
                    if self.status_data['hdop'] < 1.0:
                        self.status_data['signal_quality'] = 'excellent'
                    elif self.status_data['hdop'] < 2.0:
                        self.status_data['signal_quality'] = 'good'
                    elif self.status_data['hdop'] < 5.0:
                        self.status_data['signal_quality'] = 'moderate'
                    else:
                        self.status_data['signal_quality'] = 'poor'

                # Update fix status
                if msg.gps_qual > 0:
                    self.has_fix = True
                    self.status_data['status'] = 'position'
                    self.status_data['last_fix_time'] = datetime.utcnow().isoformat() + 'Z'

                    # Only update position data if we have valid values
                    if msg.latitude and msg.longitude:
                        self.position_data['latitude'] = float(msg.latitude)
                        self.position_data['longitude'] = float(msg.longitude)
                        self.position_data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

                    if msg.altitude:
                        self.position_data['altitude'] = float(msg.altitude)
                else:
                    self.has_fix = False
                    self.status_data['status'] = 'searching'
                    if old_fix_status and not self.fix_lost_time:
                        self.fix_lost_time = current_time

            elif isinstance(msg, pynmea2.RMC) and msg.status == 'A':
                # Only update if we have valid values
                if msg.spd_over_grnd:
                    self.position_data['speed'] = float(msg.spd_over_grnd) * 1.852  # Convert knots to km/h

                if msg.true_course:
                    self.position_data['heading'] = float(msg.true_course)

            elif isinstance(msg, pynmea2.GSA):
                fix_type = 'No Fix'
                if msg.mode_fix_type == '3':
                    fix_type = '3D'
                elif msg.mode_fix_type == '2':
                    fix_type = '2D'

                self.status_data['fix_type'] = fix_type

                if msg.hdop:
                    self.status_data['hdop'] = float(msg.hdop)

            elif isinstance(msg, pynmea2.GSV):
                if msg.msg_num == 1:  # First message in sequence
                    self.status_data['satellites_visible'] = int(msg.num_sv_in_view) if msg.num_sv_in_view else 0

        except Exception as e:
            logging.error(f"Error parsing NMEA data: {e}")

        # If we've lost fix for more than 5 seconds, clear position data
        if not self.has_fix and self.fix_lost_time and (current_time - self.fix_lost_time > 5):
            logging.info("Fix lost for more than 5 seconds, clearing position data")
            self.position_data['latitude'] = None
            self.position_data['longitude'] = None
            self.position_data['altitude'] = None
            self.position_data['speed'] = None
            self.position_data['heading'] = None
            self.fix_lost_time = 0

        # If we regained fix, reset the fix lost timer
        if self.has_fix and self.fix_lost_time:
            self.fix_lost_time = 0

        self.last_update = current_time

    def get_position_json(self):
        # Only return position data if we have valid coordinates
        if self.position_data['latitude'] is not None and self.position_data['longitude'] is not None:
            # Create a copy with no None values
            position_copy = {}
            for key, value in self.position_data.items():
                if value is not None:
                    position_copy[key] = value
            return json.dumps(position_copy)
        return None

    def get_status_json(self):
        # Create a copy with no None values
        status_copy = {}
        for key, value in self.status_data.items():
            if value is not None:
                status_copy[key] = value
        return json.dumps(status_copy)

def main():
    logging.info("Starting GPS monitoring...")
    gps_data = GPSData()
    last_status_time = 0
    last_position_time = 0
    reconnect_delay = 1
    max_reconnect_delay = 30

    # Memory management variables
    last_gc_time = time.time()
    gc_interval = 60  # Run garbage collection every 60 seconds

    while True:
        try:
            # Periodic garbage collection to prevent memory leaks
            current_time = time.time()
            if current_time - last_gc_time > gc_interval:
                gc.collect()
                last_gc_time = current_time
                logging.debug("Ran garbage collection")

            if not client.is_connected():
                connect_mqtt()

            global ser
            ser = connect_serial()

            if not ser:
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            reconnect_delay = 1

            # Count consecutive errors
            error_count = 0
            max_errors = 5

            while True:
                try:
                    # Check if data is available before reading
                    if ser.in_waiting:
                        line = ser.readline()
                        if not line:
                            error_count += 1
                            if error_count > max_errors:
                                logging.warning(f"No data received after {max_errors} attempts")
                                error_count = 0
                            continue

                        # Reset error count on successful read
                        error_count = 0

                        try:
                            line_str = line.decode('ascii', errors='replace').strip()
                            if not line_str.startswith('$'):
                                continue  # Not a NMEA sentence

                            msg = pynmea2.parse(line_str)
                            gps_data.update_from_nmea(msg)

                            current_time = time.time()

                            # Position data at 4Hz (every 0.25 seconds) when we have a fix
                            if gps_data.has_fix and current_time - last_position_time >= 0.25:
                                position_json = gps_data.get_position_json()
                                if position_json:
                                    client.publish(MQTT_TOPIC_POSITION, position_json, qos=1)
                                    logging.info(f"Position: Lat={gps_data.position_data['latitude']}, "
                                          f"Lon={gps_data.position_data['longitude']}, "
                                          f"Alt={gps_data.position_data['altitude']}, "
                                          f"Speed={gps_data.position_data['speed']}, "
                                          f"Heading={gps_data.position_data['heading']}")
                                last_position_time = current_time

                            # Status updates at 1Hz regardless of fix status
                            if current_time - last_status_time >= 1.0:
                                status_json = gps_data.get_status_json()
                                client.publish(MQTT_TOPIC_STATUS, status_json, qos=0)
                                logging.info(f"Status: {gps_data.status_data['status']}, "
                                      f"Sats={gps_data.status_data['satellites_used']}/{gps_data.status_data['satellites_visible']}, "
                                      f"Fix={gps_data.status_data['fix_type']}, "
                                      f"HDOP={gps_data.status_data['hdop']}, "
                                      f"Quality={gps_data.status_data['signal_quality']}, "
                                      f"Uptime={gps_data.status_data['uptime']}s")
                                last_status_time = current_time

                        except pynmea2.ParseError:
                            continue
                    else:
                        # No data waiting, short sleep to prevent CPU hogging
                        time.sleep(0.01)

                except serial.SerialException as e:
                    logging.error(f"Serial error: {e}")
                    # Try to close and reopen the connection
                    try:
                        if ser and ser.is_open:
                            ser.close()
                    except:
                        pass
                    break

                except Exception as e:
                    logging.error(f"Error: {e}")
                    error_count += 1
                    if error_count > max_errors:
                        logging.warning(f"Too many errors, reconnecting...")
                        break
                    continue

        except KeyboardInterrupt:
            # This should be caught by the signal handler
            break
        except Exception as e:
            logging.error(f"Main loop error: {e}")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    # Final cleanup (should be handled by signal handler, but just in case)
    try:
        if ser is not None and ser.is_open:
            ser.close()
        client.loop_stop()
        client.disconnect()
    except:
        pass

if __name__ == "__main__":
    main()
