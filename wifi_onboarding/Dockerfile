FROM ghcr.io/home-assistant/aarch64-base:latest

# Install basic packages
RUN apk update && apk add --no-cache bash curl jq

# Install networking tools for direct network management
RUN apk add --no-cache \
    hostapd \
    dnsmasq \
    wpa_supplicant \
    dhcpcd \
    iproute2 \
    wireless-tools \
    iw \
    procps \
    util-linux

# Install Python and packages
RUN apk add --no-cache \
    python3 \
    py3-pip

# Install Flask and requests via pip (required for web interface and API calls)
RUN pip3 install --no-cache-dir --break-system-packages flask requests

# Install GPIO libraries (with error handling for different architectures)
RUN pip3 install --no-cache-dir --break-system-packages gpiozero || echo "gpiozero not available for this architecture"
RUN pip3 install --no-cache-dir --break-system-packages RPi.GPIO || echo "RPi.GPIO not available for this architecture"

# Try to install lgpio for Pi 5 support (may not be available in all repos)
RUN pip3 install --no-cache-dir --break-system-packages lgpio || echo "lgpio not available"

# Create necessary directories
RUN mkdir -p /data /tmp /var/log

# Copy application files
COPY onboarding.py /onboarding.py
COPY button_monitor.py /button_monitor.py
COPY run.sh /run.sh

# Make scripts executable
RUN chmod +x /run.sh /onboarding.py /button_monitor.py

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose web interface port
EXPOSE 80

# Use run.sh as entrypoint
CMD ["/run.sh"]
RUN mkdir -p /data /tmp /var/log

# Copy application files
COPY onboarding.py /onboarding.py
COPY button_monitor.py /button_monitor.py
COPY run.sh /run.sh

# Make scripts executable
RUN chmod +x /run.sh /onboarding.py /button_monitor.py

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose web interface port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:80/health || exit 1

# Use run.sh as entrypoint
CMD ["/run.sh"]