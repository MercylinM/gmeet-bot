FROM ultrafunk/undetected-chromedriver

RUN mkdir -p /app /app/recordings /app/screenshots

WORKDIR /app

# Install system dependencies
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
    usermod -aG pulse-access root

# Environment variables - FIXED: Use consistent display and proper runtime paths
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
ENV XDG_RUNTIME_DIR=/tmp/runtime-root
ENV PULSE_RUNTIME_PATH=/tmp/pulse
ENV BACKEND_URL="https://add-on-backend.onrender.com"
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_RESOLUTION=1280x1024
ENV COLOR_DEPTH=24
ENV DISPLAY=:99

# Create runtime directories with proper permissions
RUN mkdir -p /tmp/runtime-root /tmp/pulse /run/dbus /var/run/dbus && \
    chmod 755 /run/dbus /var/run/dbus && \
    chmod 700 /tmp/runtime-root /tmp/pulse && \
    dbus-uuidgen > /var/lib/dbus/machine-id

# Clean up pulse directories
RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

# Copy application files
COPY . /app

# Additional setup - FIXED: Use root user
RUN echo 'root ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    touch /root/.Xauthority && \
    chmod 600 /root/.Xauthority

# Copy PulseAudio configuration if it exists
RUN if [ -f pulseaudio.conf ]; then mv pulseaudio.conf /etc/dbus-1/system.d/pulseaudio.conf; fi

# Remove the setup_audio.sh script creation - this is now handled in entrypoint.sh
# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]