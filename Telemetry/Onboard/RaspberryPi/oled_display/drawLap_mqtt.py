import json
import time
import paho.mqtt.client as mqtt
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from PIL import Image, ImageDraw, ImageFont

# Initialize I2C and OLED device
serial = i2c(port=1, address=0x3D)
device = ssd1309(serial)

# Fonts
try:
    medium_font = ImageFont.truetype("DejaVuSans.ttf", 10)
    large_font = ImageFont.truetype("DejaVuSans.ttf", 12)
except IOError:
    medium_font = ImageFont.load_default()
    large_font = ImageFont.load_default()

# Race data
current_lap = 0
total_laps = 0
lap_time = 0
diff_time = 0

# Lap timer variables
lap_start_time = time.time()
current_time = 0
last_lap_number = 0

def format_time(seconds):
    """Format seconds into MM:SS format"""
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes:02d}:{seconds:02d}"

def update_current_time():
    global current_time
    current_time = int(time.time() - lap_start_time)

def draw_lap_info():
    # Update current time
    update_current_time()

    # Create a new image for left half only
    image = Image.new('1', (64, 64), 0)
    draw = ImageDraw.Draw(image)

    # Draw lap counter
    draw.text((32, 5), f"{current_lap}/{total_laps}",
              fill="white", font=large_font, anchor="mt")

    # Draw horizontal line
    draw.line([(0, 20), (63, 20)], fill="white", width=1)

    # Draw current time (C)
    draw.text((32, 25), f"C: {format_time(current_time)}",
              fill="white", font=medium_font, anchor="mt")

    # Draw last lap time (L)
    if lap_time > 0:
        draw.text((32, 40), f"L: {format_time(lap_time)}",
              fill="white", font=medium_font, anchor="mt")

    # Draw difference time (D)
    if diff_time != 0:
        sign = "+" if diff_time > 0 else ""
        draw.text((32, 55), f"D: {sign}{format_time(abs(diff_time))}",
              fill="white", font=medium_font, anchor="mt")

    try:
        # Get current display content
        current_display = device.display

        # Create a new full image
        full_image = Image.new('1', (128, 64), 0)

        # Try to get the right half of the current display
        try:
            # This is a safer way to access the display buffer
            buffer = list(device.display.getdata())
            right_half = Image.new('1', (64, 64), 0)

            # Copy pixel by pixel (this is a fallback method)
            for y in range(64):
                for x in range(64):
                    if buffer[(y * 128) + x + 64]:
                        right_half.putpixel((x, y), 1)

            # Paste the right half
            full_image.paste(right_half, (64, 0))
        except:
            # If that fails, just leave the right side blank
            pass

        # Paste our new left half
        full_image.paste(image, (0, 0))

        # Update the display
        device.display(full_image)

    except Exception as e:
        print(f"Display update error: {e}")
        # Fallback: just update with our image
        device.display(image)

# MQTT Callbacks
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe("laps")

def on_message(client, userdata, msg):
    global current_lap, total_laps, lap_time, diff_time, lap_start_time, last_lap_number

    try:
        # Parse lap data
        data = json.loads(msg.payload.decode())
        print(f"Received lap data: {data}")

        if "lap_number" in data:
            new_lap = data["lap_number"]
            # If lap number changed, reset the lap timer
            if new_lap != last_lap_number:
                lap_start_time = time.time()
                last_lap_number = new_lap
            current_lap = new_lap

        if "total_laps" in data:
            total_laps = data["total_laps"]

        if "lap_time" in data:
            lap_time = data["lap_time"]

        if "diff_time" in data:
            diff_time = data["diff_time"]

        # Update display
        draw_lap_info()
    except Exception as e:
        print(f"Error processing message: {e}")

# Set up MQTT client
client = mqtt.Client()
client.username_pw_set("eco", "marathon")
client.on_connect = on_connect
client.on_message = on_message

# Initial display
draw_lap_info()

# Connect and start loop
try:
    client.connect("tome.lu", 1883, 60)

    # Start the MQTT client loop in the background
    client.loop_start()

    # Main loop to update the current time display
    while True:
        draw_lap_info()
        time.sleep(1)  # Update every second

except KeyboardInterrupt:
    print("Program terminated")
finally:
    client.loop_stop()
    client.disconnect() 