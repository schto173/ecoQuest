# Scripts

## GPS Sensor 
location: /opt/gps.py
writing to MQTT: 
- gps/postition
- gps/status

Example:
{
  "timestamp": "2025-05-02T09:03:34.475626Z",
  "latitude": 49.60029566666667,
  "longitude": 6.1263076666666665,
  "altitude": 293.7,
  "speed": 1.4927120000000003,
  "heading": 121.95
}

{
  "status": "position",
  "satellites_used": 8,
  "satellites_visible": 0,
  "hdop": 1.7,
  "fix_type": "3D",
  "last_fix_time": "2025-05-02T09:03:33.689155Z",
  "uptime": 6,
  "signal_quality": "good"
}