# FROM ultrafunk/undetected-chromedriver

# RUN mkdir /app /app/recordings /app/screenshots

# WORKDIR /app

# # Install system dependencies including portaudio and audio libraries
# RUN apt-get update && \
#     apt-get install -y \
#     python3 \
#     python3-pip \
#     pulseaudio \
#     pulseaudio-utils \
#     pavucontrol \
#     curl \
#     sudo \
#     xvfb \
#     libnss3-tools \
#     ffmpeg \
#     portaudio19-dev \
#     libasound2-dev \
#     libjack-dev \
#     libpulse-dev \
#     xdotool \
#     unzip \
#     x11vnc \
#     libfontconfig \
#     libfreetype6 \
#     xfonts-cyrillic \
#     xfonts-scalable \
#     fonts-liberation \
#     fonts-ipafont-gothic \
#     fonts-wqy-zenhei \
#     xterm \
#     vim \
#     dbus-x11 \
#     && rm -rf /var/lib/apt/lists/*

# # User and permission setup
# RUN usermod -aG audio root && \
#     adduser root pulse-access

# # Environment variables
# ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
# ENV XDG_RUNTIME_DIR=/run/user/0
# ENV BACKEND_URL="https://add-on-backend.onrender.com"
# ENV X_SERVER_NUM=1
# ENV SCREEN_WIDTH=1280
# ENV SCREEN_HEIGHT=1024
# ENV SCREEN_RESOLUTION=1280x1024
# ENV COLOR_DEPTH=24
# ENV DISPLAY=:${X_SERVER_NUM}.0

# # Audio environment variables for sounddevice
# ENV SOUNDDEVICE_IGNORE_ALSA_CONFIG=1
# ENV PULSE_RUNTIME_PATH=/run/pulse
# ENV PULSE_SERVER=unix:/run/pulse/native

# # D-Bus setup
# RUN mkdir -p /run/dbus && \
#     chmod 755 /run/dbus && \
#     mkdir -p /var/run/dbus && \
#     dbus-uuidgen > /var/lib/dbus/machine-id

# # Create pulseaudio runtime directory
# RUN mkdir -p /run/pulse && \
#     chmod 755 /run/pulse

# # Clean up pulse directories
# RUN rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

# # Copy requirements first for better caching
# COPY requirements.txt /app/
# RUN pip3 install --no-cache-dir -r requirements.txt gunicorn sounddevice numpy

# # Copy application files
# COPY . /app

# # Additional setup
# RUN echo 'user ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers && \
#     touch /root/.Xauthority && \
#     chmod 600 /root/.Xauthority

# # Copy PulseAudio configuration
# RUN mv pulseaudio.conf /etc/dbus-1/system.d/pulseaudio.conf

# # Make entrypoint executable
# RUN chmod +x /app/entrypoint.sh

# CMD ["/app/entrypoint.sh"]

# Use a base image, for example, Ubuntu
FROM ultrafunk/undetected-chromedriver

RUN mkdir /app /app/recordings /app/screenshots

# Set the working directory inside the container
WORKDIR /app

# Fix GPG key issue and install necessary dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gnupg && \
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
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
    # PipeWire dependencies
    pipewire \
    pipewire-pulse \
    pipewire-tools \
    wireplumber \
    libspa-0.2-jack \
    libspa-0.2-alsa \
    libspa-0.2-bluez5 \
    libspa-0.2-echo-cancel \
    libspa-0.2-fd \
    libspa-0.2-jackdbus \
    libspa-0.2-metadata \
    libspa-0.2-rtkit \
    libspa-0.2-v4l2 \
    # PortAudio dependencies
    portaudio19-dev \
    libasound2-dev \
    libjack-dev && \
    rm -rf /var/lib/apt/lists/*

RUN usermod -aG audio root
RUN adduser root pulse-access

ENV key=value DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
ENV XDG_RUNTIME_DIR=/run/user/0

# Create PipeWire runtime directory
RUN mkdir -p /run/pipewire
RUN chmod 755 /run/pipewire

# Clean up any existing PipeWire configuration
RUN rm -rf /var/run/pipewire /var/lib/pipewire /root/.config/pipewire

RUN mkdir -p /run/dbus
RUN chmod 755 /run/dbus

RUN dbus-daemon --system --fork

RUN wget http://files.portaudio.com/archives/pa_stable_v190700_20210406.tgz

RUN tar -xvf pa_stable_v190700_20210406.tgz

RUN mv portaudio /usr/src/

WORKDIR /usr/src/portaudio

RUN ./configure && \
    make && \
    make install && \
    ldconfig

RUN pip3 install \
    pyaudio \
    click \
    opencv-python \
    Pillow \
    sounddevice \
    websockets \
    numpy \
    selenium \
    undetected-chromedriver \
    requests

RUN echo 'user ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers

RUN adduser root pulse-access

RUN mkdir -p /var/run/dbus

RUN dbus-uuidgen > /var/lib/dbus/machine-id

WORKDIR /app

# Copy your application code to the container
COPY . /app

# Set any environment variables if required
ENV BACKEND_URL="https://add-on-backend.onrender.com"
ENV X_SERVER_NUM=1
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_RESOLUTION=1280x1024
ENV COLOR_DEPTH=24
ENV DISPLAY=:${X_SERVER_NUM}.0

# PipeWire environment variables
ENV PIPEWIRE_RUNTIME_DIR=/run/pipewire
ENV PIPEWIRE_MODULE_DIR=/usr/lib/pipewire-0.3

# PulseAudio compatibility environment variables (for PipeWire)
ENV PULSE_RUNTIME_PATH=/run/pulse
ENV PULSE_SERVER=unix:/run/pulse/native

RUN touch /root/.Xauthority
RUN chmod 600 /root/.Xauthority

RUN rm /run/dbus/pid
RUN mv pipewire.conf /etc/pipewire/pipewire.conf
RUN mv pipewire-dbus.conf /etc/dbus-1/system.d/pipewire-dbus.conf

RUN chmod +x /app/entrypoint.sh

# Define the command to run your application
CMD ["/app/entrypoint.sh"]