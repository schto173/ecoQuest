import time
import math
import json
import threading
from paho.mqtt import client as mqtt_client
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from PIL import Image, ImageDraw, ImageFont

# --- Configuration ---
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_USER = "eco"
MQTT_PASSWORD = "marathon"
MQTT_TOPIC_GPS = "gps/status"
MQTT_TOPIC_SPEED = "car/speed"
WHEEL_SPEED_FILE = '/tmp/wheel_speed.json'
WHEEL_CIRCUMFERENCE_M = 2.1 # <<<--- ADJUST THIS VALUE (meters)
RECONNECT_DELAY_S = 5.0 # Delay between MQTT reconnect attempts

# --- Global State ---
mqtt_connected = False
last_reconnect_attempt = 0
race_data = {
    "current_lap": 0,
    "total_laps": 0,
    "last_lap_time_seconds": None,
    "race_finished": False,
    "last_line_crossed": None,
    "current_lap_elapsed_seconds": None
}

# --- Initialize Display ---
try:
    serial = i2c(port=1, address=0x3D)
    device = ssd1309(serial)
except Exception as e:
    print(f"CRITICAL: Error initializing OLED display: {e}")
    exit(1) # Exit if display fails to initialize

# --- Fonts ---
try:
    tick_font = ImageFont.truetype("DejaVuSans.ttf", 8)
    error_font = ImageFont.truetype("DejaVuSans.ttf", 8) # Font for error icon
except IOError:
    print("Warning: tick_font/error_font not found, using default.")
    tick_font = ImageFont.load_default()
    error_font = ImageFont.load_default()
try:
    digital_font = ImageFont.truetype("DejaVuSans.ttf", 26)
except IOError:
    print("Warning: digital_font not found, using default.")
    digital_font = ImageFont.load_default()
try:
    lap_info_font = ImageFont.truetype("DejaVuSans.ttf", 20)
except IOError:
    print("Warning: lap_info_font not found, using default.")
    lap_info_font = ImageFont.load_default()
try:
    time_info_font = ImageFont.truetype("DejaVuSans.ttf", 10)
except IOError:
    print("Warning: time_info_font not found, using default.")
    time_info_font = ImageFont.load_default()

# --- Tachometer Drawing Functions (User's Code) ---
center_x = 132
center_y = 68
inner_radius = 48
outer_radius = 58
start_angle = 180
end_angle = 90
max_speed = 50
end_y_offset = 15

def point_on_arc(radius, angle_deg):
    angle_rad = math.radians(angle_deg)
    x = center_x + int(radius * math.cos(angle_rad))
    y = center_y - int(radius * math.sin(angle_rad))
    if abs(angle_deg - end_angle) < 1e-6:
        y += end_y_offset
    return (x, y)

def draw_arc_outline(draw):
    for angle_deg in range(int(end_angle), int(start_angle) + 1):
        try:
            draw.point(point_on_arc(inner_radius, angle_deg), fill="white")
            draw.point(point_on_arc(outer_radius, angle_deg), fill="white")
        except Exception as e:
            print(f"Error drawing arc point: {e}") # Catch potential math errors

def draw_speed_ticks(draw):
    tick_length = 4
    label_offset = 8
    if max_speed <= 0: return
    for tick in range(5, int(max_speed) + 1, 5):
        try:
            angle = start_angle - ((start_angle - end_angle) * (tick / max_speed))
            outer_pt = point_on_arc(outer_radius, angle)
            inner_pt = point_on_arc(outer_radius - tick_length, angle)
            draw.line([inner_pt, outer_pt], fill="white", width=1)
            if (tick % 10 == 0):
                label_pt = point_on_arc(outer_radius + label_offset, angle)
                draw.text(label_pt, str(tick), fill="white", font=tick_font, anchor="mm")
        except Exception as e:
            print(f"Error drawing speed tick {tick}: {e}")

def draw_needle(draw, angle_deg):
    try:
        start_pt = point_on_arc(inner_radius, angle_deg)
        end_pt = point_on_arc(outer_radius, angle_deg)
        draw.line([start_pt, end_pt], fill="white", width=2)
    except Exception as e:
        print(f"Error drawing needle: {e}")

# --- Helper Functions ---
def format_time(seconds):
    if seconds is None: return "00:00"
    try:
        seconds = float(seconds)
        if seconds < 0: return "00:00"
        minutes = int(seconds // 60)
        remaining_seconds = round(seconds % 60)
        if remaining_seconds == 60:
            minutes += 1
            remaining_seconds = 0
        return f"{minutes:02d}:{remaining_seconds:02d}"
    except (TypeError, ValueError): return "00:00"

def calculate_speed_kmh(rpm):
    if WHEEL_CIRCUMFERENCE_M <= 0: return 0.0
    try:
        speed_mps = (float(rpm) * WHEEL_CIRCUMFERENCE_M) / 60
        speed_kmh = speed_mps * 3.6
        return speed_kmh
    except (TypeError, ValueError): return 0.0

def read_speed():
    """Reads RPM and calculates speed. Returns 0.0 on any error."""
    try:
        with open(WHEEL_SPEED_FILE, 'r') as f:
            data = json.load(f)
            rpm = data.get('rpm', 0.0)
            speed_kmh = calculate_speed_kmh(rpm)
            # Return speed capped by the gauge's max_speed for display
            return min(speed_kmh, max_speed)
    except Exception as e: # Catch all exceptions during file read/parse
        # print(f"Warning: Could not read/parse speed file: {e}") # Reduce noise
        return 0.0

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        print("MQTT: Connected successfully.")
        mqtt_connected = True
        try:
            client.subscribe(MQTT_TOPIC_GPS, qos=1)
            print(f"MQTT: Subscribed to {MQTT_TOPIC_GPS}")
        except Exception as e:
            print(f"MQTT: Error subscribing: {e}")
            mqtt_connected = False # Treat as disconnected if subscribe fails
    else:
        print(f"MQTT: Connection failed with code: {rc}")
        mqtt_connected = False

def on_disconnect(client, userdata, rc, properties=None):
    global mqtt_connected, last_reconnect_attempt
    print(f"MQTT: Disconnected with code: {rc}. Will attempt reconnect.")
    mqtt_connected = False
    last_reconnect_attempt = 0 # Allow immediate reconnect attempt check

def on_message(client, userdata, msg):
    global race_data
    # print(f"MQTT: Received message on {msg.topic}") # Debug
    if msg.topic == MQTT_TOPIC_GPS:
        try:
            payload = json.loads(msg.payload.decode())
            required_keys = ["current_lap", "total_laps", "last_lap_time_seconds",
                             "race_finished", "current_lap_elapsed_seconds"]
            if all(key in payload for key in required_keys):
                 race_data.update(payload)
            # else: # Reduce noise
            #    print(f"Warning: Incomplete MQTT message on {msg.topic}")
        except json.JSONDecodeError:
            print(f"Error decoding MQTT JSON on {msg.topic}")
        except Exception as e:
            print(f"Error processing MQTT message on {msg.topic}: {e}")

# --- MQTT Client Setup ---
client = mqtt_client.Client(client_id="oled_display_robust", protocol=mqtt_client.MQTTv5)
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

# --- Main Application Logic ---
def attempt_mqtt_connect():
    """Attempts to connect MQTT client, non-blocking."""
    global last_reconnect_attempt
    now = time.time()
    # Throttle reconnect attempts
    if not mqtt_connected and (now - last_reconnect_attempt > RECONNECT_DELAY_S):
        last_reconnect_attempt = now
        print("MQTT: Attempting to connect...")
        try:
            # Use connect_async for non-blocking connection attempt
            client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
            # loop_start() should already be running if previously started
            # If it's the very first attempt, start the loop here
            if not client.is_connected(): # Check if loop needs starting
                 try:
                     client.loop_start()
                     print("MQTT: Network loop started.")
                 except RuntimeError: # loop already running
                     pass
                 except Exception as e:
                     print(f"MQTT: Error starting loop: {e}")

        except Exception as e:
            print(f"MQTT: Connection attempt failed: {e}")
            # Ensure loop is stopped if connect fails badly, though loop_start might handle this
            try:
                client.loop_stop(force=True) # Force stop if connect failed
            except: pass


# --- Initial MQTT Connection Attempt ---
attempt_mqtt_connect() # Try initial connection

# --- Main Display Loop ---
print("Starting main display loop...")
try:
    while True:
        # 1. Read Speed (Critical Path) - Do this first
        current_speed_kmh = read_speed()

        # 2. Prepare Drawing Surface
        try:
            image = Image.new('1', (device.width, device.height), 0)
            draw = ImageDraw.Draw(image)
        except Exception as e:
            print(f"CRITICAL: Failed to create image buffer: {e}")
            time.sleep(1) # Avoid busy-looping if image creation fails
            continue # Skip rest of the loop iteration

        # 3. Draw UI Elements
        # --- Draw Lap Information ---
        try:
            total_laps_display = race_data.get('total_laps', 0)
            if not isinstance(total_laps_display, int) or total_laps_display < 0: total_laps_display = 0
            current_lap_display = race_data.get('current_lap', 0)
            if not isinstance(current_lap_display, int) or current_lap_display < 0: current_lap_display = 0

            draw.text((60, 20), f"{current_lap_display}/{total_laps_display}", fill="white", font=lap_info_font, anchor="rb")
            current_time_str = format_time(race_data.get('current_lap_elapsed_seconds'))
            draw.text((0, 40), f"THIS {current_time_str}", fill="white", font=time_info_font)
            last_time_str = format_time(race_data.get('last_lap_time_seconds'))
            draw.text((0, 52), f"LAST {last_time_str}", fill="white", font=time_info_font)
            # Add Finished indicator if needed
            # if race_data.get('race_finished', False): draw.text((5, 20), "FIN", fill="white", font=lap_info_font)
        except Exception as e:
            print(f"Error drawing lap info: {e}")

        # --- Draw MQTT Status Icon ---
        if not mqtt_connected:
            try:
                draw.text((74, 0), "!", fill="white", font=error_font) # Error icon at 74,0
            except Exception as e:
                 print(f"Error drawing MQTT status icon: {e}")

        # --- Draw Tachometer ---
        try:
            draw_arc_outline(draw)
            draw_speed_ticks(draw)
            if max_speed > 0:
                speed_for_gauge = min(current_speed_kmh, max_speed)
                needle_angle = start_angle - ((start_angle - end_angle) * (speed_for_gauge / max_speed))
            else:
                needle_angle = start_angle
            draw_needle(draw, needle_angle)
            draw.text((128, 64), f"{int(current_speed_kmh)}", fill="white", font=digital_font, anchor="rb")
        except Exception as e:
            print(f"Error drawing tachometer elements: {e}")


        # 4. Update Display (Critical Path)
        try:
            device.display(image)
        except Exception as e:
            # Log error but continue, as speed reading is the priority
            print(f"Warning: Error updating OLED display: {e}")

        # 5. Handle MQTT Connection & Publishing
        attempt_mqtt_connect() # Check if reconnect is needed

        if mqtt_connected:
            try:
                # Publish calculated speed in km/h
                speed_payload = json.dumps({"speed_kmh": round(current_speed_kmh, 2), "timestamp": time.time()})
                client.publish(MQTT_TOPIC_SPEED, speed_payload, qos=0)
            except Exception as e:
                # Log error but don't crash; connection might drop right after check
                print(f"Warning: Error publishing speed via MQTT: {e}")
                # Consider setting mqtt_connected = False here if publish fails repeatedly

        # 6. Loop Delay
        time.sleep(0.5) # Target ~2Hz refresh rate

except KeyboardInterrupt:
    print("\nCtrl+C detected. Shutting down...")
except Exception as e:
    print(f"CRITICAL: An unexpected error occurred in the main loop: {e}")
finally:
    # --- Cleanup ---
    print("Stopping MQTT loop...")
    # Stop the network loop thread
    client.loop_stop(force=True) # Force stop may be needed if loop is stuck
    # Disconnect is usually handled by loop_stop, but call explicitly if needed
    # try:
    #     client.disconnect()
    # except: pass
    print("MQTT client stopped.")
    # Clear the display on exit
    try:
        print("Clearing display...")
        device.clear()
        device.hide()
    except Exception as e:
        print(f"Warning: Error clearing/hiding display: {e}")
    print("Exiting script.")