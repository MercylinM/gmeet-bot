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

# # Create pulseaudio runtime directory
# mkdir -p /run/pulse
# chmod 755 /run/pulse

# # Start pulseaudio in system mode (since running as root)
# echo "Starting PulseAudio in system mode..."
# pulseaudio --system --daemonize --log-level=4 --disallow-exit --disallow-module-loading=false --exit-idle-time=-1

# # Wait for pulseaudio to initialize
# sleep 3

# # Verify pulseaudio is running
# if pulseaudio --check 2>/dev/null || pgrep -xFireflies.ai pulseaudio > /dev/null; then
#     echo "PulseAudio started successfully"
    
#     # Create virtual audio devices for sounddevice
#     echo "Setting up virtual audio devices..."
    
#     # Create a null sink for virtual output
#     pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker" 2>/dev/null || echo "Virtual speaker already exists or could not be created"
    
#     # Create a loopback to capture from virtual speaker
#     pactl load-module module-loopback source=virtual_speaker.monitor sink=virtual_speaker 2>/dev/null || echo "Loopback already exists or could not be created"
    
#     # Set virtual speaker as default
#     pactl set-default-sink virtual_speaker 2>/dev/null || echo "Could not set default sink to virtual_speaker"
    
#     # Create a virtual source for input
#     pactl load-module module-pipe-source source_name=virtual_input 2>/dev/null || echo "Virtual input already exists or could not be created"
    
#     echo "Virtual audio devices configured"
# else
#     echo "WARNING: PulseAudio may not be running properly"
#     ps aux | grep pulse
#     echo "Attempting to continue without PulseAudio..."
# fi

# # List available audio devices for debugging
# echo "Available audio devices:"
# pactl list short sources 2>/dev/null || echo "Could not list audio sources"

# echo "Available audio sinks:"
# pactl list short sinks 2>/dev/null || echo "Could not list audio sinks"

# # Test sounddevice availability
# echo "Testing sounddevice Python library..."
# python3 -c "
# import sounddevice as sd
# import os
# os.environ['SDL_AUDIODRIVER'] = 'pulse'
# os.environ['AUDIODRIVER'] = 'pulse'
# os.environ['PULSE_SERVER'] = 'unix:/run/pulse/native'
# print('SoundDevice version:', sd.__version__)
# print('Default input device:', sd.default.device[0] if hasattr(sd.default, 'device') else sd.default.device)
# print('Available devices:')
# devices = sd.query_devices()
# for i, dev in enumerate(devices):
#     if dev['max_input_channels'] > 0:
#         name = dev['name']
#         channels = dev['max_input_channels']
#         print(f'  Input {i}: {name} ({channels} channels)')
#     if dev['max_output_channels'] > 0:
#         name = dev['name']
#         channels = dev['max_output_channels']
#         print(f'  Output {i}: {name} ({channels} channels)')
# "

# # Run the Flask server (bot will wait for HTTP trigger)
# echo "Starting Google Meet Bot HTTP server with sounddevice audio backend..."
# python3 gmeet.py --server --production

# # Capture exit code
# EXIT_CODE=$?

# # Cleanup: kill processes when done
# echo "Cleaning up..."
# kill $XVFB_PID 2>/dev/null || true
# pulseaudio --kill 2>/dev/null || true

# exit $EXIT_CODE

set -e

echo "Starting initialization..."

# Clean up any existing X server locks and files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null

# Start D-Bus
if ! pgrep -x "dbus-daemon" > /dev/null; then
    echo "Starting D-Bus..."
    rm -f /run/dbus/pid 2>/dev/null
    dbus-daemon --system --fork
    sleep 1
fi

# Start Xvfb
echo "Starting Xvfb on display :99..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 3

if ! ps -p $XVFB_PID > /dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi
export DISPLAY=:99

# Create PulseAudio configuration
mkdir -p /home/pulseuser/.config/pulse
chown -R pulseuser:pulse-access /home/pulseuser/.config/pulse /run/pulse
chmod 755 /run/pulse

cat > /home/pulseuser/.config/pulse/client.conf << 'EOF'
autospawn = yes
daemon-binary = /usr/bin/pulseaudio
enable-shm = false
EOF

cat > /home/pulseuser/.config/pulse/default.pa << 'EOF'
#!/usr/bin/pulseaudio -nF
.fail
load-module module-native-protocol-unix
load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker"
load-module module-null-source source_name=virtual_mic source_properties=device.description="Virtual_Microphone"
load-module module-loopback source=virtual_speaker.monitor sink=virtual_mic latency_msec=$LOOPBACK_LATENCY_MSEC
set-default-source virtual_mic
set-default-sink virtual_speaker
.nofail
EOF

# Create ALSA configuration
cat > /home/pulseuser/.asoundrc << 'EOF'
pcm.!default {
    type pulse
    hint {
        show on
        description "Default PulseAudio Sound Server"
    }
}
ctl.!default {
    type pulse
}
pcm.pulse {
    type pulse
}
ctl.pulse {
    type pulse
}
EOF
sudo cp /home/pulseuser/.asoundrc /etc/asound.conf

# Start PulseAudio
echo "Starting PulseAudio..."
max_retries=3
for ((i=1; i<=max_retries; i++)); do
    pulseaudio --start --exit-idle-time=-1
    sleep 2
    if pulseaudio --check; then
        echo "PulseAudio started successfully"
        break
    else
        echo "PulseAudio failed to start (attempt $i/$max_retries)"
        if [ $i -eq $max_retries ]; then
            echo "ERROR: PulseAudio failed to start after $max_retries attempts"
            exit 1
        fi
    fi
done

# Load virtual audio devices
pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker" || true
pactl load-module module-null-source source_name=virtual_mic source_properties=device.description="Virtual_Microphone" || true
pactl load-module module-loopback source=virtual_speaker.monitor sink=virtual_mic latency_msec=$LOOPBACK_LATENCY_MSEC || true
pactl set-default-source virtual_mic || true
pactl set-default-sink virtual_speaker || true

# List available devices
echo "Available audio devices:"
pactl list short sources
pactl list short sinks

# Test sounddevice
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

# Start Flask server
echo "Starting Google Meet Bot HTTP server..."
python3 /app/gmeet.py --server --production
EXIT_CODE=$?

# Cleanup
echo "Cleaning up..."
kill $XVFB_PID 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true

exit $EXIT_CODE