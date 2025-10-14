FROM ultrafunk/undetected-chromedriver

RUN mkdir /app /app/recordings /app/screenshots

WORKDIR /app

# Install system dependencies including portaudio and audio libraries
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
    portaudio19-dev \
    libasound2-dev \
    libjack-dev \
    libpulse-dev \
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

# Audio environment variables for sounddevice
ENV SOUNDDEVICE_IGNORE_ALSA_CONFIG=1
ENV PULSE_RUNTIME_PATH=/run/pulse
ENV PULSE_SERVER=unix:/run/pulse/native

# D-Bus setup
RUN mkdir -p /run/dbus && \
    chmod 755 /run/dbus && \
    mkdir -p /var/run/dbus && \
    dbus-uuidgen > /var/lib/dbus/machine-id

# Create pulseaudio runtime directory
RUN mkdir -p /run/pulse && \
    chmod 755 /run/pulse

# Clean up pulse directories
RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

# Copy requirements first for better caching
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn sounddevice numpy

# Copy application files
COPY . /app

# Additional setup
RUN echo 'user ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    touch /root/.Xauthority && \
    chmod 600 /root/.Xauthority

# Copy PulseAudio configuration
RUN mv pulseaudio.conf /etc/dbus-1/system.d/pulseaudio.conf

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]