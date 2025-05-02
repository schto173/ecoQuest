import time, math
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from PIL import Image, ImageDraw, ImageFont

# Initialize I2C and OLED device (address 0x3D)
serial = i2c(port=1, address=0x3D)
device = ssd1309(serial)

# Arc (gauge) parameters
center_x = 132   # Moved to the right for proper visibility
center_y = 68    # Moved downward for proper visibility
inner_radius = 58
outer_radius = 68
start_angle = 180  # Angle corresponding to 0 speed (arc's bottom)
end_angle = 90     # Angle corresponding to top speed (arc's top-right)
max_speed = 50

# Additional offset to lower the endpoint (only on the y axis)
end_y_offset = 15  # Adjust this value as needed

# Font for tick labels (tiny font)
try:
    tick_font = ImageFont.truetype("DejaVuSans.ttf", 8)
except IOError:
    tick_font = ImageFont.load_default()

# Digital speed readout font (bigger)
try:
    digital_font = ImageFont.truetype("DejaVuSans.ttf", 32)
except IOError:
    digital_font = ImageFont.load_default()

def point_on_arc(radius, angle_deg):
    """Calculate a point on an arc given a radius and angle (in degrees)."""
    angle_rad = math.radians(angle_deg)
    x = center_x + int(radius * math.cos(angle_rad))
    y = center_y - int(radius * math.sin(angle_rad))
    # For the endpoint (end_angle), adjust the y coordinate downward
    if angle_deg == end_angle:
        y += end_y_offset
    return (x, y)

def draw_arc_outline(draw):
    """Draw the outline of the gauge (inner and outer arcs as points)."""
    for angle in range(end_angle, start_angle + 1):
        draw.point(point_on_arc(inner_radius, angle), fill="white")
        draw.point(point_on_arc(outer_radius, angle), fill="white")

def draw_speed_ticks(draw):
    """Draw tick marks and labels for speeds 5, 10, …, 50 along the arc,
    placing the labels outside the arc."""
    tick_length = 4    # Length of the tick mark in pixels
    label_offset = 8   # Positive offset places labels outside the arc
    for tick in range(5, max_speed + 1, 5):
        # Compute the angle for this tick mark
        angle = start_angle - ((start_angle - end_angle) * (tick / max_speed))
        # Calculate outer and inner points for the tick mark
        outer_pt = point_on_arc(outer_radius, angle)
        inner_pt = point_on_arc(outer_radius - tick_length, angle)
        draw.line([inner_pt, outer_pt], fill="white", width=1)
        # Calculate label position (outside the arc)
        label_pt = point_on_arc(outer_radius + label_offset, angle)
        if (tick % 10 == 0):
            draw.text(label_pt, str(tick), fill="white", font=tick_font, anchor="mm")

def draw_needle(draw, angle_deg):
    """Draw a single line (the needle) at the specified angle."""
    draw.line([point_on_arc(inner_radius, angle_deg),
               point_on_arc(outer_radius, angle_deg)],
              fill="white", width=2)

# Animate speed from 0 to max_speed then stop.
speed = 0
while speed <= max_speed:
    # Create a new blank monochrome image (128x64)
    image = Image.new('1', (128, 64), 0)
    draw = ImageDraw.Draw(image)
    
    # Draw the static arc outline and tick marks with labels
    draw_arc_outline(draw)
    draw_speed_ticks(draw)
    
    # Compute the needle angle (linear mapping from start_angle to end_angle)
    needle_angle = start_angle - ((start_angle - end_angle) * (speed / max_speed))
    
    # Draw the needle (a single line)
    draw_needle(draw, needle_angle)
    
    # Draw the digital speed readout (big number) inside the display.
    draw.text((128, 64), f"{speed}", fill="white", font=digital_font, anchor="rb")
    
    # Update the display
    device.display(image)
    
    time.sleep(0.1)
    speed += 1

# Once top speed is reached, hold the display
time.sleep(3)
