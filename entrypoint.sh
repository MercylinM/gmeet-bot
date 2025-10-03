Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99; python3 gmeet.py

#!/bin/bash

# Clean up any existing X server locks and files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null

# Start Xvfb on display 99
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to start properly
sleep 2

# Check if Xvfb is running
if ! ps -p $XVFB_PID > /dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

echo "Xvfb started successfully on display :99"

# Set display for all applications
export DISPLAY=:99

# Start pulseaudio (without sudo)
pulseaudio --start --log-level=0

# Wait a moment for pulseaudio
sleep 1

# Run your application
echo "Starting Google Meet recorder..."
python3 gmeet.py

# Cleanup: kill Xvfb when done
kill $XVFB_PID 2>/dev/null