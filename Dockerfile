# Base image with Chrome & Python
FROM ultrafunk/undetected-chromedriver:latest

# Set noninteractive mode to avoid prompts
ENV DEBIAN_FRONTEND=noninteractive

# Create app directory
WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    pulseaudio \
    pulseaudio-utils \
    dbus-x11 \
    xvfb \
    ffmpeg \
    curl \
    unzip \
    x11vnc \
    xdotool \
    libnss3-tools \
    libxss1 \
    libappindicator3-1 \
    libfontconfig \
    libfreetype6 \
    fonts-liberation \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    alsa-utils \
    xfonts-base \
    vim \
    sudo \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 -s /bin/bash myuser && \
    usermod -aG audio,pulse-access myuser

# Environment vars
ENV HOME=/home/myuser
ENV XDG_RUNTIME_DIR=/run/user/1000
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
ENV PULSE_RUNTIME_PATH=/run/user/1000/pulse
ENV DISPLAY=:99

# Setup runtime directories
RUN mkdir -p /run/user/1000 && \
    chown -R myuser:myuser /run/user/1000

# Copy app code
COPY . /app

# Install Python deps
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Switch to non-root user
USER myuser

# Entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
