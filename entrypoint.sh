#!/bin/bash

set -e  

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

# Create pulseaudio runtime directory
mkdir -p /run/pulse
chmod 755 /run/pulse

# Start pulseaudio in system mode (since running as root)
echo "Starting PulseAudio in system mode..."
pulseaudio --system --daemonize --log-level=4 --disallow-exit --disallow-module-loading=false --exit-idle-time=-1

# Wait for pulseaudio to initialize
sleep 3

# Verify pulseaudio is running
if pulseaudio --check 2>/dev/null || pgrep -x pulseaudio > /dev/null; then
    echo "PulseAudio started successfully"
    
    # Create virtual audio devices for sounddevice
    echo "Setting up virtual audio devices..."
    
    # Create a null sink for virtual output
    pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker" 2>/dev/null || echo "Virtual speaker already exists or could not be created"
    
    # Create a loopback to capture from virtual speaker
    pactl load-module module-loopback source=virtual_speaker.monitor sink=virtual_speaker 2>/dev/null || echo "Loopback already exists or could not be created"
    
    # Set virtual speaker as default
    pactl set-default-sink virtual_speaker 2>/dev/null || echo "Could not set default sink to virtual_speaker"
    
    echo "Virtual audio devices configured"
else
    echo "WARNING: PulseAudio may not be running properly"
    ps aux | grep pulse
    echo "Attempting to continue without PulseAudio..."
fi

# List available audio devices for debugging
echo "Available audio devices:"
pactl list short sources 2>/dev/null || echo "Could not list audio sources"

echo "Available audio sinks:"
pactl list short sinks 2>/dev/null || echo "Could not list audio sinks"

# Test sounddevice availability
# Test sounddevice availability
echo "Testing sounddevice Python library..."
python3 -c "
import sounddevice as sd
print('SoundDevice version:', sd.__version__)
print('Default input device:', sd.default.device[0] if hasattr(sd.default, 'device') else sd.default.device)
print('Available devices:')
devices = sd.query_devices()
for i, dev in enumerate(devices):
    if dev['max_input_channels'] > 0:
        name = dev['name']
        channels = dev['max_input_channels']
        print(f'  Input {i}: {name} ({channels} channels)')
    if dev['max_output_channels'] > 0:
        name = dev['name']
        channels = dev['max_output_channels']
        print(f'  Output {i}: {name} ({channels} channels)')
"

# Run the Flask server (bot will wait for HTTP trigger)
echo "Starting Google Meet Bot HTTP server with sounddevice audio backend..."
python3 gmeet.py --server --production

# Capture exit code
EXIT_CODE=$?

# Cleanup: kill processes when done
echo "Cleaning up..."
kill $XVFB_PID 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true

exit $EXIT_CODE