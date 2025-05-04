#!/usr/bin/env python3

import paho.mqtt.client as mqtt_client
import json
import time
import signal
import os

# --- Configuration ---
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_USER = "eco"
MQTT_PASSWORD = "marathon" # Replace with your actual password
MQTT_TOPIC_SPEED = "speed/data" # Topic to publish speed data to

WHEEL_SPEED_FILE = '/tmp/wheel_speed.json'
PUBLISH_RATE_HZ = 4 # Target publish rate (times per second)
PUBLISH_INTERVAL_S = 1.0 / PUBLISH_RATE_HZ

# --- Global State ---
client = None
mqtt_connected = False
running = True # Flag to control the main loop

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc, properties=None):
    """Callback when the client connects to the MQTT broker."""
    global mqtt_connected
    if rc == 0:
        print(f"MQTT: Connected successfully to {MQTT_BROKER}.")
        mqtt_connected = True
    else:
        print(f"MQTT: Connection failed with code: {rc}. Check broker details.")
        mqtt_connected = False
        # Optional: Add retry logic here if loop_start's built-in reconnect isn't sufficient

def on_disconnect(client, userdata, rc, properties=None):
     """Callback when the client disconnects."""
     global mqtt_connected
     print(f"MQTT: Disconnected from broker with code: {rc}.")
     mqtt_connected = False
     if rc != 0:
         print("MQTT: Unexpected disconnection.")

# --- Helper Functions ---
def read_speed_data():
    """Reads and parses the JSON data from the speed file."""
    try:
        # Check if file exists and is not empty
        if not os.path.exists(WHEEL_SPEED_FILE) or os.path.getsize(WHEEL_SPEED_FILE) == 0:
            # print(f"Warning: Speed file '{WHEEL_SPEED_FILE}' not found or is empty.")
            return None # Return None if file doesn't exist or is empty

        with open(WHEEL_SPEED_FILE, 'r') as f:
            data = json.load(f)
            # Optional: Add validation here if needed (e.g., check for specific keys)
            return data
    except FileNotFoundError:
        # This case is handled above, but kept for robustness
        # print(f"Warning: Speed file '{WHEEL_SPEED_FILE}' not found.")
        return None
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{WHEEL_SPEED_FILE}'. File content might be corrupted.")
        return None
    except Exception as e:
        print(f"Error: Could not read speed file '{WHEEL_SPEED_FILE}': {e}")
        return None

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    global running, client
    print("\nCtrl+C detected. Shutting down speed publisher...")
    running = False # Signal the main loop to stop
    # Cleanup happens in the finally block after the loop exits

# --- Main Execution ---
if __name__ == "__main__":
    print("Starting MQTT Speed Publisher...")
    signal.signal(signal.SIGINT, signal_handler) # Setup Ctrl+C handler

    # Create MQTT client instance
    client_id = f"speed_publisher_{os.getpid()}" # Unique client ID
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    # Assign callbacks
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    loop_started = False
    try:
        print(f"Attempting to connect to MQTT broker: {MQTT_BROKER}:{MQTT_PORT}...")
        client.connect_async(MQTT_BROKER, MQTT_PORT, 60) # Connect non-blocking
        client.loop_start() # Start network loop in background thread
        loop_started = True
        print("MQTT: Network loop started.")

        # Wait briefly for connection to establish
        connect_timeout = 5 # seconds
        start_wait = time.time()
        while not mqtt_connected and (time.time() - start_wait) < connect_timeout:
            time.sleep(0.1)

        if not mqtt_connected:
            print(f"MQTT: Failed to connect within {connect_timeout} seconds. Exiting.")
            running = False # Prevent loop from starting if connection failed initially

        print(f"Starting publishing loop (Target: {PUBLISH_RATE_HZ} Hz)...")
        last_publish_time = time.time()

        while running:
            loop_start_time = time.time()

            if mqtt_connected:
                speed_data = read_speed_data()

                if speed_data is not None:
                    try:
                        payload_str = json.dumps(speed_data)
                        result, mid = client.publish(MQTT_TOPIC_SPEED, payload=payload_str, qos=0, retain=False) # QoS 0 for speed

                        if result == mqtt_client.MQTT_ERR_SUCCESS:
                             # print(f"Published: {payload_str}") # Uncomment for verbose logging
                             pass
                        else:
                             print(f"MQTT: Failed to publish message (Error code: {result})")
                             # If publish fails consistently, might indicate connection issue despite flag
                             # Consider setting mqtt_connected = False here if errors persist

                    except TypeError as e:
                        print(f"Error: Could not serialize speed data to JSON: {e} (Data: {speed_data})")
                    except Exception as e:
                        print(f"Error during MQTT publish: {e}")
                # else:
                    # print("No speed data to publish.") # Uncomment if you want to see when file is missing/empty

            else:
                # print("MQTT not connected. Skipping publish.") # Uncomment for verbose logging
                # Reconnect is handled by loop_start, but we wait here
                time.sleep(1) # Wait longer if not connected

            # Calculate time taken and sleep to maintain rate
            time_taken = time.time() - loop_start_time
            sleep_duration = max(0, PUBLISH_INTERVAL_S - time_taken)
            time.sleep(sleep_duration)

    except KeyboardInterrupt:
        # This is caught by the signal handler now, but keep for safety
        print("KeyboardInterrupt caught in main block.")
        running = False
    except Exception as e:
        print(f"An unexpected error occurred in the main loop: {e}")
        running = False
    finally:
        print("Cleaning up...")
        if client:
            if loop_started:
                print("MQTT: Stopping network loop...")
                client.loop_stop()
            if mqtt_connected: # Attempt disconnect only if loop was potentially connected
                 print("MQTT: Disconnecting client...")
                 client.disconnect()
        print("Speed publisher finished.")
