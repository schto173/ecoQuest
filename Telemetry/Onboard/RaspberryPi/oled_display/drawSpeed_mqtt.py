import math
import paho.mqtt.client as mqtt
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from PIL import Image, ImageDraw, ImageFont

# Initialize I2C and OLED device
serial = i2c(port=1, address=0x3D)
device = ssd1309(serial)

# Arc parameters
center_x = 132
center_y = 68
inner_radius = 58
outer_radius = 68
start_angle = 180
end_angle = 90

# Fonts
try:
    tick_font = ImageFont.truetype("DejaVuSans.ttf", 8)
except IOError:
    tick_font = ImageFont.load_default()

try:
    digital_font = ImageFont.truetype("DejaVuSans.ttf", 28)
except IOError:
    digital_font = ImageFont.load_default()

def point_on_arc(radius, angle_deg):
    angle_rad = math.radians(angle_deg)
    x = center_x + int(radius * math.cos(angle_rad))
    y = center_y - int(radius * math.sin(angle_rad))
    if angle_deg == end_angle:
        y += 15  # end_y_offset
    return (x, y)

def draw_speed(speed):
    # Create a new image for right half only
    image = Image.new('1', (64, 64), 0)
    draw = ImageDraw.Draw(image)

    # Adjust coordinates for right half (subtract 64 from all x coordinates)
    adjusted_center_x = center_x - 64

    # Helper function with adjusted coordinates
    def adjusted_point_on_arc(radius, angle_deg):
        angle_rad = math.radians(angle_deg)
        x = adjusted_center_x + int(radius * math.cos(angle_rad))
        y = center_y - int(radius * math.sin(angle_rad))
        if angle_deg == end_angle:
            y += 15  # end_y_offset
        return (x, y)

    # Draw arc outline
    for angle in range(end_angle, start_angle + 1):
        draw.point(adjusted_point_on_arc(inner_radius, angle), fill="white")
        draw.point(adjusted_point_on_arc(outer_radius, angle), fill="white")

    # Draw speed ticks
    for tick in range(5, 51, 5):
        angle = start_angle - ((start_angle - end_angle) * (tick / 50))
        outer_pt = adjusted_point_on_arc(outer_radius, angle)
        inner_pt = adjusted_point_on_arc(outer_radius - 4, angle)
        draw.line([inner_pt, outer_pt], fill="white", width=1)
        label_pt = adjusted_point_on_arc(outer_radius + 8, angle)
        draw.text(label_pt, str(tick), fill="white", font=tick_font, anchor="mm")

    # Draw needle
    needle_angle = start_angle - ((start_angle - end_angle) * (float(speed) / 50))
    draw.line([adjusted_point_on_arc(inner_radius, needle_angle),
               adjusted_point_on_arc(outer_radius, needle_angle)],
              fill="white", width=2)

    # Draw digital speed (adjusted for right half)
    draw.text((64, 64), f"{speed:.0f}", fill="white", font=digital_font, anchor="rb")

    try:
        # Get current display content
        current_display = device.display

        # Create a new full image
        full_image = Image.new('1', (128, 64), 0)

        # Try to get the left half of the current display
        try:
            # This is a safer way to access the display buffer
            buffer = list(device.display.getdata())
            left_half = Image.new('1', (64, 64), 0)

            # Copy pixel by pixel (this is a fallback method)
            for y in range(64):
                for x in range(64):
                    if buffer[(y * 128) + x]:
                        left_half.putpixel((x, y), 1)

            # Paste the left half
            full_image.paste(left_half, (0, 0))
        except:
            # If that fails, just leave the left side blank
            pass

        # Paste our new right half
        full_image.paste(image, (64, 0))

        # Update the display
        device.display(full_image)

    except Exception as e:
        print(f"Display update error: {e}")
        # Fallback: just update with our image
        device.display(image)

# MQTT Callbacks
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe("speed")

def on_message(client, userdata, msg):
    try:
        speed_value = float(msg.payload.decode())
        print(f"Received speed: {speed_value}")
        # Directly update display when message is received
        draw_speed(speed_value)
    except ValueError:
        print(f"Invalid speed value: {msg.payload.decode()}")

# Set up MQTT client
client = mqtt.Client()
client.username_pw_set("eco", "marathon")
client.on_connect = on_connect
client.on_message = on_message

# Initial display with speed 0
draw_speed(0)

# Connect and start loop
try:
    client.connect("tome.lu", 1883, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("Program terminated")
finally:
    client.disconnect()