import time
import math
import json
from paho.mqtt import client as mqtt_client
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from PIL import Image, ImageDraw, ImageFont

# --- Configuration ---
MQTT_BROKER = "tome.lu"
MQTT_PORT = 1883
MQTT_USER = "eco"          # Updated User
MQTT_PASSWORD = "marathon" # Updated Password
MQTT_TOPIC_GPS = "gps/status"
MQTT_TOPIC_SPEED = "car/speed" # Topic to publish calculated speed
WHEEL_SPEED_FILE = '/tmp/wheel_speed.json'
WHEEL_CIRCUMFERENCE_M = 2.1 # <<<--- ADJUST THIS VALUE (meters) for accurate speed

# Initialize I2C and OLED device (address 0x3D)
try:
    serial = i2c(port=1, address=0x3D)
    device = ssd1309(serial)
except Exception as e:
    print(f"Error initializing OLED display: {e}")
    exit(1)

# --- Start of User's Adjusted Tachometer Code ---

# Arc (gauge) parameters
center_x = 132   # Moved to the right for proper visibility
center_y = 68    # Moved downward for proper visibility
inner_radius = 48  # Updated Radius
outer_radius = 58  # Updated Radius
start_angle = 180  # Angle corresponding to 0 speed (arc's bottom)
end_angle = 90     # Angle corresponding to top speed (arc's top-right)
max_speed = 50     # Max speed to display on the gauge (km/h)

# Additional offset to lower the endpoint (only on the y axis)
end_y_offset = 15  # Adjust this value as needed

# Font for tick labels (tiny font)
try:
    tick_font = ImageFont.truetype("DejaVuSans.ttf", 8)
except IOError:
    print("Warning: tick_font not found, using default.")
    tick_font = ImageFont.load_default()

# Digital speed readout font (bigger)
try:
    # Updated digital font size
    digital_font = ImageFont.truetype("DejaVuSans.ttf", 26)
except IOError:
    print("Warning: digital_font not found, using default.")
    digital_font = ImageFont.load_default()

# Font for lap info (using tick_font size)
try:
    # Updated lap info font
    lap_info_font = ImageFont.truetype("DejaVuSans.ttf", 20)
except IOError:
    print("Warning: lap_info_font not found, using default.")
    lap_info_font = ImageFont.load_default() # Fallback

try:
    # Added time info font
    time_info_font = ImageFont.truetype("DejaVuSans.ttf", 10)
except IOError:
    print("Warning: time_info_font not found, using default.")
    time_info_font = ImageFont.load_default() # Fallback


def point_on_arc(radius, angle_deg):
    """Calculate a point on an arc given a radius and angle (in degrees)."""
    angle_rad = math.radians(angle_deg)
    x = center_x + int(radius * math.cos(angle_rad))
    y = center_y - int(radius * math.sin(angle_rad))
    # For the endpoint (end_angle), adjust the y coordinate downward
    if abs(angle_deg - end_angle) < 1e-6: # Use tolerance for float comparison
        y += end_y_offset
    return (x, y)

def draw_arc_outline(draw):
    """Draw the outline of the gauge (inner and outer arcs as points)."""
    # Ensure range includes the end_angle
    # Use int() for range arguments if angles can be floats
    for angle_deg in range(int(end_angle), int(start_angle) + 1):
        draw.point(point_on_arc(inner_radius, angle_deg), fill="white")
        draw.point(point_on_arc(outer_radius, angle_deg), fill="white")


def draw_speed_ticks(draw):
    """Draw tick marks and labels for speeds 5, 10, …, max_speed along the arc,
    placing the labels outside the arc."""
    tick_length = 4    # Length of the tick mark in pixels
    label_offset = 8   # Positive offset places labels outside the arc
    if max_speed <= 0:
        return
    # Use int() for range arguments
    for tick in range(5, int(max_speed) + 1, 5):
        # Compute the angle for this tick mark
        angle = start_angle - ((start_angle - end_angle) * (tick / max_speed))
        # Calculate outer and inner points for the tick mark
        outer_pt = point_on_arc(outer_radius, angle)
        inner_pt = point_on_arc(outer_radius - tick_length, angle)
        draw.line([inner_pt, outer_pt], fill="white", width=1)
        # Calculate label position (outside the arc)
        label_pt = point_on_arc(outer_radius + label_offset, angle)
        # Draw labels only for multiples of 10
        if (tick % 10 == 0):
            draw.text(label_pt, str(tick), fill="white", font=tick_font, anchor="mm")

def draw_needle(draw, angle_deg):
    """Draw a single line (the needle) at the specified angle."""
    start_pt = point_on_arc(inner_radius, angle_deg)
    end_pt = point_on_arc(outer_radius, angle_deg)
    draw.line([start_pt, end_pt], fill="white", width=2)

# --- End of User's Adjusted Tachometer Code ---

# Global variables for race data
race_data = {
    "current_lap": 0,
    "total_laps": 0,
    "last_lap_time_seconds": None,
    "race_finished": False,
    "last_line_crossed": None,
    "current_lap_elapsed_seconds": None
}

def format_time(seconds):
    """Format time value in MM:SS format, handles None."""
    # Updated format to MM:SS (no decimals)
    if seconds is None:
        return "00:00"
    try:
        seconds = float(seconds)
        if seconds < 0:
             return "00:00"
        minutes = int(seconds // 60)
        # Use round() for seconds part before formatting
        remaining_seconds = round(seconds % 60)
        # Handle case where rounding pushes seconds to 60
        if remaining_seconds == 60:
            minutes += 1
            remaining_seconds = 0
        return f"{minutes:02d}:{remaining_seconds:02d}" # Format seconds with 2 digits
    except (TypeError, ValueError):
        return "00:00"

def calculate_speed_kmh(rpm):
    """Calculates speed in km/h from RPM and wheel circumference."""
    if WHEEL_CIRCUMFERENCE_M <= 0:
        return 0.0
    speed_mps = (rpm * WHEEL_CIRCUMFERENCE_M) / 60
    speed_kmh = speed_mps * 3.6
    return speed_kmh

def read_speed():
    """Reads RPM from JSON file and calculates speed in km/h."""
    # This function remains the same as the previous version
    try:
        with open(WHEEL_SPEED_FILE, 'r') as f:
            data = json.load(f)
            rpm = data.get('rpm', 0.0)
            if not isinstance(rpm, (int, float)):
                rpm = 0.0
            speed_kmh = calculate_speed_kmh(rpm)
            # Return speed capped by the gauge's max_speed for display consistency
            # The actual calculated speed might be higher, but the gauge won't show it
            return min(speed_kmh, max_speed)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError) as e:
        # print(f"Warning: Could not read or parse speed file '{WHEEL_SPEED_FILE}': {e}")
        return 0.0

# MQTT Callbacks
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to MQTT broker")
        client.subscribe(MQTT_TOPIC_GPS, qos=1)
    else:
        print(f"Failed to connect to MQTT broker with code: {rc}")

def on_message(client, userdata, msg):
    global race_data
    if msg.topic == MQTT_TOPIC_GPS:
        try:
            payload = json.loads(msg.payload.decode())
            required_keys = ["current_lap", "total_laps", "last_lap_time_seconds",
                             "race_finished", "current_lap_elapsed_seconds"]
            if all(key in payload for key in required_keys):
                 race_data.update(payload)
            else:
                print(f"Warning: Received incomplete MQTT message on {msg.topic}, skipping update. Payload: {payload}")
        except json.JSONDecodeError:
            print(f"Error decoding MQTT message on {msg.topic}")
        except Exception as e:
            print(f"Error processing MQTT message on {msg.topic}: {e}")

def on_disconnect(client, userdata, rc, properties=None):
    print(f"Disconnected from MQTT broker with code: {rc}")

# Setup MQTT client with protocol v5
client = mqtt_client.Client(client_id="oled_display_client_v2", protocol=mqtt_client.MQTTv5) # Unique client_id
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

# Connect to MQTT broker
try:
    print(f"Connecting to MQTT broker {MQTT_BROKER}...")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
except Exception as e:
    print(f"Failed to connect to MQTT broker: {e}")
    exit(1)

# Main loop
try:
    while True:
        # Read current speed (calculated from RPM)
        current_speed_kmh = read_speed()

        # Create a new blank monochrome image (128x64)
        image = Image.new('1', (device.width, device.height), 0)
        draw = ImageDraw.Draw(image)

        # --- Draw Lap Information on Left Side (User's New Layout) ---

        # Lap counter
        total_laps_display = race_data.get('total_laps', 0)
        if not isinstance(total_laps_display, int) or total_laps_display < 0:
            total_laps_display = 0
        current_lap_display = race_data.get('current_lap', 0)
        if not isinstance(current_lap_display, int) or current_lap_display < 0:
            current_lap_display = 0

        # Use updated font and position
        draw.text((60, 20),
                 f"{current_lap_display}/{total_laps_display}",
                 fill="white", font=lap_info_font, anchor="rb")

        # Current lap time
        current_time_str = format_time(race_data.get('current_lap_elapsed_seconds'))
        # Use updated font and position
        draw.text((0, 40),
                 f"THIS {current_time_str}",
                 fill="white", font=time_info_font)

        # Last lap time
        last_time_str = format_time(race_data.get('last_lap_time_seconds'))
        # Use updated font and position
        draw.text((0, 52),
                 f"LAST {last_time_str}",
                 fill="white", font=time_info_font)

        # Race finished indicator (Position needs adjustment if used with new layout)
        # If you want the finished indicator, decide where it should go.
        # Example: draw.text((5, 20), "FINISHED!", fill="white", font=lap_info_font)
        # if race_data.get('race_finished', False):
        #    draw.text((x_start, y_start + 4 * line_height), # Old position, needs update
        #             "FINISHED!",
        #             fill="white", font=lap_info_font)


        # --- Draw Tachometer (User's Adjusted Parameters) ---
        draw_arc_outline(draw)
        draw_speed_ticks(draw)

        # Compute the needle angle based on calculated speed
        if max_speed > 0:
            # Ensure speed doesn't exceed max_speed for angle calculation
            speed_for_gauge = min(current_speed_kmh, max_speed)
            needle_angle = start_angle - ((start_angle - end_angle) * (speed_for_gauge / max_speed))
        else:
            needle_angle = start_angle

        # Draw the needle
        draw_needle(draw, needle_angle)

        # Draw the digital speed readout (using calculated speed)
        # Use the user's original position but updated font
        draw.text((128, 64), f"{int(current_speed_kmh)}", fill="white", font=digital_font, anchor="rb")

        # --- Update Display and Publish ---
        try:
            device.display(image)
        except Exception as e:
            print(f"Error updating display: {e}")

        if client.is_connected():
            try:
                # Publish calculated speed in km/h
                speed_payload = json.dumps({"speed_kmh": round(current_speed_kmh, 2), "timestamp": time.time()})
                # Publish with QoS 0 for frequent updates
                client.publish(MQTT_TOPIC_SPEED, speed_payload, qos=0)
            except Exception as e:
                print(f"Error publishing speed: {e}")

        time.sleep(0.5)

except KeyboardInterrupt:
    print("\nCtrl+C detected. Shutting down...")
except Exception as e:
    print(f"An unexpected error occurred in the main loop: {e}")
finally:
    print("Stopping MQTT loop...")
    client.loop_stop()
    print("Disconnecting MQTT client...")
    time.sleep(1)
    print("MQTT connection closed.")
    try:
        print("Clearing display...")
        device.clear()
        device.hide()
    except Exception as e:
        print(f"Error clearing/hiding display: {e}")
    print("Exiting script.")