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
    dbus-x11 \
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
    alsa-utils \
    libxss1 \
    libappindicator3-1 \
    && rm -rf /var/lib/apt/lists/*

# Add root to audio groups
RUN usermod -aG audio,pulse-access root

# Setup environment variables
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
ENV XDG_RUNTIME_DIR=/tmp/runtime-root
ENV PULSE_RUNTIME_PATH=/tmp/pulse
ENV BACKEND_URL="https://add-on-backend.onrender.com"
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_RESOLUTION=1280x1024
ENV COLOR_DEPTH=24
ENV DISPLAY=:99

# Create runtime and dbus directories with correct permissions
RUN mkdir -p /tmp/runtime-root /tmp/pulse /run/dbus /var/run/dbus && \
    chmod 755 /run/dbus /var/run/dbus && \
    chmod 700 /tmp/runtime-root /tmp/pulse && \
    dbus-uuidgen > /var/lib/dbus/machine-id

# Clean up PulseAudio configs to avoid stale files
RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

COPY . /app

# Allow root passwordless sudo
RUN echo 'root ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    touch /root/.Xauthority && chmod 600 /root/.Xauthority

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
