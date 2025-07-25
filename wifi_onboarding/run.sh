#!/bin/bash
set -e

echo "[INFO] Starting WiFi Onboarding Add-on - Direct Network Tools"

# Get configuration from Home Assistant options
CONFIG_PATH="/data/options.json"
if [ -f "$CONFIG_PATH" ]; then
    DEBUG=$(jq -r '.debug // false' "$CONFIG_PATH")
    GPIO_PIN=$(jq -r '.gpio_pin // 17' "$CONFIG_PATH")
    HOLD_TIME=$(jq -r '.hold_time // 5' "$CONFIG_PATH")
    HOTSPOT_SSID=$(jq -r '.hotspot_ssid // "WiFi-Setup"' "$CONFIG_PATH")
    ENABLE_BUTTON=$(jq -r '.enable_button // true' "$CONFIG_PATH")
    CONNECTION_TIMEOUT=$(jq -r '.connection_timeout // 60' "$CONFIG_PATH")
else
    echo "[WARNING] No options.json found, using defaults"
    DEBUG=false
    GPIO_PIN=17
    HOLD_TIME=5
    HOTSPOT_SSID="WiFi-Setup"
    ENABLE_BUTTON=true
    CONNECTION_TIMEOUT=60
fi

echo "[INFO] Configuration:"
echo "  - Debug: $DEBUG"
echo "  - GPIO Pin: $GPIO_PIN"
echo "  - Hold Time: ${HOLD_TIME}s"
echo "  - Hotspot SSID: $HOTSPOT_SSID"
echo "  - Button Enabled: $ENABLE_BUTTON"
echo "  - Connection Timeout: ${CONNECTION_TIMEOUT}s"

# Export environment variables
export HOTSPOT_SSID="$HOTSPOT_SSID"
export CONNECTION_TIMEOUT="$CONNECTION_TIMEOUT"
export PYTHONUNBUFFERED=1

# Create necessary directories
mkdir -p /data /tmp

# Cleanup function
cleanup() {
    echo "[INFO] Cleanup requested..."
    pkill -f hostapd 2>/dev/null || true
    pkill -f dnsmasq 2>/dev/null || true
    pkill -f "python.*onboarding.py" 2>/dev/null || true
    pkill -f "python.*button_monitor.py" 2>/dev/null || true
    sleep 2
    echo "[INFO] Cleanup completed"
    exit 0
}

# Trap signals for proper cleanup
trap cleanup SIGTERM SIGINT SIGQUIT

# Check if wlan0 exists
if ip link show wlan0 >/dev/null 2>&1; then
    echo "[INFO] ✅ wlan0 interface found"
else
    echo "[WARNING] wlan0 interface not found"
    echo "[INFO] Available network interfaces:"
    ip link show || true
    echo "[WARNING] WiFi functionality may not work properly"
fi

# Kill any existing processes that might interfere
echo "[INFO] Cleaning up existing processes..."
pkill -f hostapd 2>/dev/null || true
pkill -f dnsmasq 2>/dev/null || true
pkill -f "python.*onboarding.py" 2>/dev/null || true
pkill -f "python.*button_monitor.py" 2>/dev/null || true
sleep 2

# Start button monitor in background if enabled
if [ "$ENABLE_BUTTON" = "true" ]; then
    echo "[INFO] Starting button monitor..."
    
    if [ "$DEBUG" = "true" ]; then
        python3 /button_monitor.py --pin "$GPIO_PIN" --hold "$HOLD_TIME" --debug &
    else
        python3 /button_monitor.py --pin "$GPIO_PIN" --hold "$HOLD_TIME" &
    fi
    
    BUTTON_PID=$!
    echo "[INFO] ✅ Button monitor started (PID: $BUTTON_PID)"
    
    # Brief delay to let button monitor initialize
    sleep 2
    
    # Check if button monitor is still running
    if ! kill -0 $BUTTON_PID 2>/dev/null; then
        echo "[WARNING] Button monitor failed to start properly"
        echo "[INFO] Continuing without button monitoring..."
        BUTTON_PID=""
    fi
else
    echo "[INFO] Button monitoring disabled"
    BUTTON_PID=""
fi

# Start main onboarding web server
echo "[INFO] Starting main onboarding web server..."

if [ "$DEBUG" = "true" ]; then
    python3 /onboarding.py &
else
    python3 /onboarding.py &
fi

WEB_PID=$!
echo "[INFO] ✅ Web server started (PID: $WEB_PID)"

# Brief delay to let web server initialize
sleep 3

# Check if web server is still running
if ! kill -0 $WEB_PID 2>/dev/null; then
    echo "[ERROR] Web server failed to start!"
    exit 1
fi

echo "[INFO] === ALL SERVICES STARTED ==="
echo "[INFO] ✅ Web Interface: http://192.168.4.1"
echo "[INFO] ✅ GPIO Button: Pin $GPIO_PIN (hold ${HOLD_TIME}s for reset)"
if [ -n "$BUTTON_PID" ]; then
    echo "[INFO] Services: web($WEB_PID), button($BUTTON_PID)"
else
    echo "[INFO] Services: web($WEB_PID)"
fi

# Function to check service health
check_services() {
    local services_ok=true
    
    # Check web server
    if ! kill -0 $WEB_PID 2>/dev/null; then
        echo "[ERROR] Web server died (PID: $WEB_PID)"
        services_ok=false
    fi
    
    # Check button monitor if enabled
    if [ -n "$BUTTON_PID" ] && ! kill -0 $BUTTON_PID 2>/dev/null; then
        echo "[WARNING] Button monitor died (PID: $BUTTON_PID)"
        # Don't exit for button monitor failure, just log it
        BUTTON_PID=""
    fi
    
    echo "$services_ok"
}

# Monitor services and keep container running
echo "[INFO] Monitoring services..."
HEALTH_CHECK_INTERVAL=30
LAST_STATUS_LOG=0

while true; do
    sleep 10
    
    # Check if services are healthy
    if [ "$(check_services)" = "false" ]; then
        echo "[ERROR] Critical service died - exiting"
        break
    fi
    
    # Log status periodically
    current_time=$(date +%s)
    if [ $((current_time - LAST_STATUS_LOG)) -ge $HEALTH_CHECK_INTERVAL ]; then
        echo "[INFO] Status: Services running normally"
        
        # Show network status
        if ip link show wlan0 >/dev/null 2>&1; then
            wlan0_state=$(ip addr show wlan0 | grep "state" | awk '{print $9}' || echo "UNKNOWN")
            echo "[INFO] wlan0 state: $wlan0_state"
            
            # Show IP if available
            wlan0_ip=$(ip addr show wlan0 | grep "inet " | awk '{print $2}' | head -1 || echo "")
            if [ -n "$wlan0_ip" ]; then
                echo "[INFO] wlan0 IP: $wlan0_ip"
            fi
        fi
        
        LAST_STATUS_LOG=$current_time
    fi
    
    # Check for reset flag
    if [ -f "/tmp/wifi_reset" ]; then
        echo "[INFO] Reset flag detected - restarting services"
        rm -f "/tmp/wifi_reset"
        
        # Restart only the web server (button monitor handles its own reset)
        echo "[INFO] Restarting web server..."
        kill $WEB_PID 2>/dev/null || true
        sleep 2
        
        python3 /onboarding.py &
        WEB_PID=$!
        
        if kill -0 $WEB_PID 2>/dev/null; then
            echo "[INFO] ✅ Web server restarted (PID: $WEB_PID)"
        else
            echo "[ERROR] Failed to restart web server"
            break
        fi
    fi
done

# If we get here, something went wrong
echo "[ERROR] Service monitoring loop exited"
cleanup
# update
