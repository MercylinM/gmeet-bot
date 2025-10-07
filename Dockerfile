FROM ultrafunk/undetected-chromedriver

RUN mkdir /app /app/recordings /app/screenshots

WORKDIR /app

# Install system dependencies including sox and pulseaudio-utils
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
    sox \
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

# D-Bus setup
RUN mkdir -p /run/dbus && \
    chmod 755 /run/dbus && \
    mkdir -p /var/run/dbus && \
    dbus-uuidgen > /var/lib/dbus/machine-id

# Clean up pulse directories
RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

# Install PortAudio
RUN wget http://files.portaudio.com/archives/pa_stable_v190700_20210406.tgz && \
    tar -xvf pa_stable_v190700_20210406.tgz && \
    mv portaudio /usr/src/ && \
    rm pa_stable_v190700_20210406.tgz

WORKDIR /usr/src/portaudio
RUN ./configure && \
    make && \
    make install && \
    ldconfig

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

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]