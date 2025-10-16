# #!/bin/bash

# set -e  

# echo "Starting initialization..."

# # Clean up any existing X server locks and files
# rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null

# # Start D-Bus if not running
# if ! pgrep -x "dbus-daemon" > /dev/null; then
#     echo "Starting D-Bus..."
#     rm -f /run/dbus/pid 2>/dev/null
#     dbus-daemon --system --fork
#     sleep 1
# fi

# # Start Xvfb on display 99
# echo "Starting Xvfb on display :99..."
# Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
# XVFB_PID=$!

# # Wait for Xvfb to start properly
# sleep 3

# # Check if Xvfb is running
# if ! ps -p $XVFB_PID > /dev/null; then
#     echo "ERROR: Xvfb failed to start"
#     exit 1
# fi

# echo "Xvfb started successfully on display :99"

# # Set display for all applications
# export DISPLAY=:99

# # Start pulseaudio in system mode (since running as root)
# echo "Starting PulseAudio in system mode..."
# pulseaudio --system --daemonize --log-level=4 --disallow-exit --disallow-module-loading=false

# # Wait for pulseaudio to initialize
# sleep 3

# # Verify pulseaudio is running
# if pulseaudio --check 2>/dev/null || pgrep -x pulseaudio > /dev/null; then
#     echo "PulseAudio started successfully"
#     # Create a null sink for virtual audio
#     pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker" 2>/dev/null || echo "Could not create virtual sink"
# else
#     echo "WARNING: PulseAudio may not be running properly"
#     ps aux | grep pulse
#     echo "Attempting to continue without PulseAudio..."
# fi

# # List available audio devices 
# echo "Available audio devices:"
# pactl list short sources 2>/dev/null || echo "Could not list audio sources"

# # Run the Flask server (bot will wait for HTTP trigger)
# echo "Starting Google Meet Bot HTTP server..."
# python3 gmeet.py --server --production

# # Capture exit code
# EXIT_CODE=$?

# # Cleanup: kill processes when done
# echo "Cleaning up..."
# kill $XVFB_PID 2>/dev/null || true
# pulseaudio --kill 2>/dev/null || true

# exit $

#!/bin/bash
set -e

echo "Starting initialization as user..."

# Setup runtime directories for PulseAudio
mkdir -p /run/user/1000 /run/user/1000/pulse /run/user/1000/bus
chmod 700 /run/user/1000 /run/user/1000/pulse /run/user/1000/bus

export XDG_RUNTIME_DIR=/run/user/1000
export PULSE_RUNTIME_PATH=/run/user/1000/pulse
export DISPLAY=:99

# Kill existing PulseAudio socket if any
rm -f /run/user/1000/pulse/*

# Start D-Bus if needed
if ! pgrep -x "dbus-daemon" > /dev/null; then
    echo "Starting D-Bus..."
    rm -f /run/dbus/pid 2>/dev/null
    mkdir -p /var/run/dbus
    dbus-daemon --system --fork
    sleep 2
fi

# Start Xvfb
echo "Starting Xvfb..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 3
if ! ps -p $XVFB_PID > /dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi
echo "Xvfb started successfully on display :99"

# Start PulseAudio in user mode
echo "Starting PulseAudio in user mode..."
pulseaudio --daemonize --log-level=4 --disallow-exit --exit-idle-time=-1 --disallow-module-loading --disable-shm
sleep 3

# Check if PulseAudio is running
if pactl info >/dev/null 2>&1; then
    echo "PulseAudio started successfully"
    # Set up null sink and loopback if needed
    if ! pactl list short sinks | grep -q virtual_speaker; then
        pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker"
    fi
    if ! pactl list short modules | grep -q "module-loopback.*virtual_speaker.monitor"; then
        pactl load-module module-loopback source=virtual_speaker.monitor sink=virtual_speaker latency_msec=1
    fi
    pactl set-default-sink virtual_speaker || echo "Could not set default sink"
    echo "Virtual audio setup complete"
else
    echo "WARNING: PulseAudio not properly started"
fi

# Test FFmpeg
echo "Testing FFmpeg..."
ffmpeg -version >/dev/null && echo "FFmpeg OK" || echo "FFmpeg test failed"

# Run the application
echo "Starting Google Meet Bot..."
exec python3 gmeet.py --server --production
