#!/bin/bash

set -e  # Exit on error

echo "Starting initialization..."

# Clean up any existing X server locks and files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null

# Start D-Bus if not running
if ! pgrep -x "dbus-daemon" > /dev/null; then
    echo "Starting D-Bus..."
    rm -f /run/dbus/pid 2>/dev/null
    dbus-daemon --system --fork
    sleep 1
fi

# Start Xvfb on display 99
echo "Starting Xvfb on display :99..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to start properly
sleep 3

# Check if Xvfb is running
if ! ps -p $XVFB_PID > /dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

echo "Xvfb started successfully on display :99"

# Set display for all applications
export DISPLAY=:99

# Start pulseaudio
echo "Starting PulseAudio..."
pulseaudio --start --log-level=4 --log-target=stderr &
PULSE_PID=$!

# Wait for pulseaudio to initialize
sleep 3

# Verify pulseaudio is running
if ! pulseaudio --check; then
    echo "WARNING: PulseAudio may not be running properly"
    ps aux | grep pulse
else
    echo "PulseAudio started successfully"
fi

# List available audio devices for debugging
echo "Available audio devices:"
pactl list short sources 2>/dev/null || echo "Could not list audio sources"

# Run your application
echo "Starting Google Meet recorder..."
python3 gmeet.py

# Capture exit code
EXIT_CODE=$?

# Cleanup: kill processes when done
echo "Cleaning up..."
kill $XVFB_PID 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true

exit $EXIT_CODE
