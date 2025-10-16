FROM ultrafunk/undetected-chromedriver

RUN mkdir -p /app /app/recordings /app/screenshots

WORKDIR /app

# Install dependencies
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

# Create a non-root user
RUN adduser -D myuser && \
    usermod -aG audio,pulse-access myuser

# Set environment variables for user runtime
ENV HOME /home/myuser
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
ENV XDG_RUNTIME_DIR=/run/user/1000
ENV PULSE_RUNTIME_PATH=/run/user/1000/pulse

# Setup runtime directories with proper permissions
RUN mkdir -p /run/user/1000 && \
    chown -R myuser:myuser /run/user/1000 && \
    mkdir -p $XDG_RUNTIME_DIR && \
    chown myuser:myuser $XDG_RUNTIME_DIR

# Switch to non-root user
USER myuser
WORKDIR /app

# Copy requirements
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

# Copy app files
COPY . /app

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
