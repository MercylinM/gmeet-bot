FROM ultrafunk/undetected-chromedriver

RUN mkdir /app /app/recordings /app/screenshots

WORKDIR /app

# Install system dependencies - removed sox, added ffmpeg and audio tools
RUN apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
    pulseaudio \
    pulseaudio-utils \
    pavucontrol \
    curl \
    sudo \
    xvfb \
    libnss3-tools \
    ffmpeg \
    xdotool \
    unzip \
    x11vnc \
    libfontconfig \
    libfreetype6 \
    xfonts-cyrillic \
    xfonts-scalable \
    fonts-liberation \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    xterm \
    vim \
    dbus-x11 \
    alsa-utils \
    && rm -rf /var/lib/apt/lists/*

# User and permission setup
RUN usermod -aG audio root && \
    adduser root pulse-access

# Environment variables
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
ENV XDG_RUNTIME_DIR=/run/user/0
ENV BACKEND_URL="https://add-on-backend.onrender.com"
ENV X_SERVER_NUM=1
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_RESOLUTION=1280x1024
ENV COLOR_DEPTH=24
ENV DISPLAY=:${X_SERVER_NUM}.0

# D-Bus setup
RUN mkdir -p /run/dbus && \
    chmod 755 /run/dbus && \
    mkdir -p /var/run/dbus && \
    dbus-uuidgen > /var/lib/dbus/machine-id

# Clean up pulse directories
RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

# Remove PortAudio installation (not needed for FFmpeg)
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

# Copy application files
COPY . /app

# Additional setup
RUN echo 'user ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    touch /root/.Xauthority && \
    chmod 600 /root/.Xauthority

# Copy PulseAudio configuration
RUN mv pulseaudio.conf /etc/dbus-1/system.d/pulseaudio.conf

# Create virtual audio setup script
RUN echo '#!/bin/bash\n\
    pulseaudio --start --log-level=4\n\
    sleep 2\n\
    # Create virtual sink for audio capture\n\
    pactl load-module module-null-sink sink_name=virtual_speaker sink_properties=device.description="Virtual_Speaker"\n\
    # Create loopback to capture from virtual speaker\n\
    pactl load-module module-loopback source=virtual_speaker.monitor sink=virtual_speaker\n\
    # Set virtual speaker as default\n\
    pactl set-default-sink virtual_speaker\n\
    echo "Virtual audio setup complete"\n\
    ' > /app/setup_audio.sh && chmod +x /app/setup_audio.sh

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]