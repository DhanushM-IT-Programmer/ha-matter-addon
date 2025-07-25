#!/usr/bin/env python3
"""
Fixed WiFi Onboarding - Forces 2.4GHz for better phone compatibility
"""

from flask import Flask, request, render_template_string, jsonify, redirect
import os
import json
import logging
import sys
import subprocess
import time
import signal

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

class WiFiController:
    def __init__(self):
        self.config_file = "/data/wifi_config.json"
        self.hotspot_ssid = os.getenv('HOTSPOT_SSID', 'WiFi-Setup')
        self.current_mode = "unknown"
        
    def run_command(self, cmd, timeout=30):
        """Run shell command safely"""
        try:
            logger.debug(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
            return result
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return None

    def unblock_wifi_sysfs(self):
        """Unblock WiFi using sysfs interface"""
        logger.info("🔓 Unblocking WiFi via sysfs...")
        
        try:
            rfkill_base = "/sys/class/rfkill"
            if os.path.exists(rfkill_base):
                for device in os.listdir(rfkill_base):
                    device_path = os.path.join(rfkill_base, device)
                    if os.path.isdir(device_path):
                        try:
                            # Check device type
                            type_file = os.path.join(device_path, "type")
                            if os.path.exists(type_file):
                                with open(type_file, 'r') as f:
                                    device_type = f.read().strip()
                                
                                if device_type in ['wlan', 'wifi']:
                                    # Unblock soft block
                                    soft_file = os.path.join(device_path, "soft")
                                    if os.path.exists(soft_file):
                                        with open(soft_file, 'w') as f:
                                            f.write('0')
                                        logger.info(f"✅ Unblocked {device}")
                        except Exception as e:
                            logger.debug(f"Error processing {device}: {e}")
        except Exception as e:
            logger.warning(f"sysfs unblock failed: {e}")

    def stop_services(self):
        """Stop conflicting services"""
        services = ["hostapd", "dnsmasq", "wpa_supplicant", "dhcpcd"]
        for service in services:
            self.run_command(["pkill", "-f", service])
        time.sleep(2)

    def reload_wifi_driver(self):
        """Reload WiFi driver to apply unblock changes"""
        logger.info("🔧 Reloading WiFi driver...")
        
        try:
            # Find WiFi modules
            result = self.run_command(["lsmod"])
            wifi_modules = []
            if result:
                for line in result.stdout.split('\n'):
                    if 'brcmfmac' in line or 'brcmutil' in line:
                        module = line.split()[0]
                        wifi_modules.append(module)
            
            logger.info(f"Found WiFi modules: {wifi_modules}")
            
            # Remove modules in dependency order
            remove_order = ['brcmfmac', 'brcmutil', 'cfg80211']
            for module in remove_order:
                if module in wifi_modules or module == 'cfg80211':
                    logger.info(f"Removing module: {module}")
                    self.run_command(["modprobe", "-r", module])
                    time.sleep(1)
            
            # Wait for removal
            time.sleep(3)
            
            # Reload modules in reverse order
            load_order = ['cfg80211', 'brcmutil', 'brcmfmac']
            for module in load_order:
                logger.info(f"Loading module: {module}")
                result = self.run_command(["modprobe", module])
                if result and result.returncode == 0:
                    logger.info(f"  ✅ {module} loaded")
                else:
                    logger.warning(f"  ⚠️ {module} may have failed to load")
                time.sleep(2)
            
            # Wait for interface to be ready
            time.sleep(5)
            
            # Check if wlan0 is back
            result = self.run_command(["ip", "link", "show", "wlan0"])
            if result and result.returncode == 0:
                logger.info("✅ wlan0 interface detected after driver reload")
                return True
            else:
                logger.error("❌ wlan0 interface not found after driver reload")
                return False
                
        except Exception as e:
            logger.error(f"Driver reload failed: {e}")
            return False

    def setup_interface(self):
        """Setup wlan0 interface for AP mode"""
        logger.info("🔧 Setting up wlan0 interface...")
        
        # First attempt: unblock WiFi
        self.unblock_wifi_sysfs()
        
        # Stop services
        self.stop_services()
        
        # Try to bring interface up
        self.run_command(["ip", "link", "set", "wlan0", "down"])
        time.sleep(1)
        self.run_command(["ip", "addr", "flush", "dev", "wlan0"])
        time.sleep(1)
        self.run_command(["ip", "link", "set", "wlan0", "up"])
        time.sleep(2)
        
        # Check if still blocked by testing a simple iw command
        result = self.run_command(["iw", "dev", "wlan0", "info"])
        if not result or result.returncode != 0 or "Operation not supported" in result.stderr:
            logger.warning("⚠️ Interface still appears blocked, trying driver reload...")
            
            if self.reload_wifi_driver():
                # Try unblock again after driver reload
                self.unblock_wifi_sysfs()
                
                # Reset interface again
                self.run_command(["ip", "link", "set", "wlan0", "down"])
                time.sleep(1)
                self.run_command(["ip", "addr", "flush", "dev", "wlan0"])
                time.sleep(1)
                self.run_command(["ip", "link", "set", "wlan0", "up"])
                time.sleep(2)
        
        # Assign IP
        result = self.run_command(["ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"])
        if result and result.returncode != 0 and "File exists" not in (result.stderr or ""):
            logger.error(f"Failed to assign IP: {result.stderr}")
            return False
        
        # Final verification
        result = self.run_command(["iw", "dev", "wlan0", "info"])
        if result and result.returncode == 0:
            logger.info("✅ Interface setup complete - iw command successful")
            return True
        else:
            logger.error("❌ Interface setup failed - iw command failed")
            return False

    def create_hostapd_config(self):
        """Create optimized hostapd config for 2.4GHz"""
        config_content = f"""# Optimized hostapd config for phone compatibility
interface=wlan0
driver=nl80211
ssid={self.hotspot_ssid}

# Force 2.4GHz band
hw_mode=g
channel=6
ieee80211n=1
ieee80211d=1
country_code=US

# Disable 5GHz to ensure 2.4GHz operation
ieee80211ac=0

# Basic settings
auth_algs=1
wpa=0
ignore_broadcast_ssid=0

# Power and compatibility settings
tx_power=20
beacon_int=100
dtim_period=2

# Logging
logger_syslog=-1
logger_syslog_level=2
logger_stdout=-1
logger_stdout_level=2
"""
        
        try:
            config_path = "/tmp/hostapd.conf"
            with open(config_path, 'w') as f:
                f.write(config_content)
            logger.info("✅ Created hostapd config")
            return config_path
        except Exception as e:
            logger.error(f"Failed to create hostapd config: {e}")
            return None

    def start_hostapd(self):
        """Start hostapd with better error handling"""
        logger.info("🚀 Starting hostapd...")
        
        config_path = self.create_hostapd_config()
        if not config_path:
            return False

        # Debug: Show what's actually in the config file
        try:
            with open(config_path, 'r') as f:
                config_content = f.read()
            logger.info(f"📄 Actual hostapd config file content:\n{config_content}")
        except Exception as e:
            logger.error(f"Cannot read config file: {e}")

        # Test config first
        result = self.run_command(["hostapd", "-t", config_path])
        if result and result.returncode != 0:
            logger.error(f"hostapd config test failed:")
            logger.error(f"Output: {result.stdout}")
            logger.error(f"Error: {result.stderr}")
            
            # Try to create an even simpler config
            logger.info("🔧 Trying ultra-minimal config...")
            ultra_minimal_config = f"""interface=wlan0
ssid={self.hotspot_ssid}
channel=6
"""
            try:
                with open('/tmp/hostapd_minimal.conf', 'w') as f:
                    f.write(ultra_minimal_config)
                
                result = self.run_command(["hostapd", "-t", "/tmp/hostapd_minimal.conf"])
                if result and result.returncode == 0:
                    logger.info("✅ Ultra-minimal config works!")
                    config_path = "/tmp/hostapd_minimal.conf"
                else:
                    logger.error(f"Even ultra-minimal config failed: {result.stdout}")
                    return False
            except Exception as e:
                logger.error(f"Failed to create ultra-minimal config: {e}")
                return False
        else:
            logger.info("✅ hostapd config test passed")

        # Start hostapd daemon
        try:
            process = subprocess.Popen(
                ["hostapd", "-B", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            time.sleep(5)
            
            # Check if hostapd is running
            result = self.run_command(["pgrep", "-f", "hostapd"])
            if result and result.returncode == 0:
                logger.info("✅ hostapd started successfully")
                
                # Verify AP mode
                result = self.run_command(["iw", "dev", "wlan0", "info"])
                if result and "type AP" in result.stdout:
                    logger.info("✅ Interface is in AP mode")
                    
                    # Check channel
                    if "channel" in result.stdout:
                        for line in result.stdout.split('\n'):
                            if "channel" in line.lower():
                                logger.info(f"📡 {line.strip()}")
                    
                    return True
                else:
                    logger.warning("⚠️ Interface not in AP mode")
                    return False
            else:
                logger.error("❌ hostapd process not found")
                stdout, stderr = process.communicate()
                if stderr:
                    logger.error(f"hostapd error: {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start hostapd: {e}")
            return False

    def start_dnsmasq(self):
        """Start dnsmasq for DHCP"""
        logger.info("🚀 Starting dnsmasq...")
        
        config_content = """interface=wlan0
bind-interfaces
dhcp-range=192.168.4.10,192.168.4.50,12h
dhcp-option=3,192.168.4.1
dhcp-option=6,192.168.4.1
server=8.8.8.8
address=/#/192.168.4.1
no-resolv
no-hosts
"""
        
        try:
            config_path = "/tmp/dnsmasq.conf"
            with open(config_path, 'w') as f:
                f.write(config_content)
            
            # Start dnsmasq
            process = subprocess.Popen([
                "dnsmasq", "--conf-file=" + config_path, "-k"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            time.sleep(3)
            
            # Check if running
            result = self.run_command(["pgrep", "-f", "dnsmasq"])
            if result and result.returncode == 0:
                logger.info("✅ dnsmasq started successfully")
                return True
            else:
                stdout, stderr = process.communicate()
                logger.error(f"dnsmasq failed: {stderr.decode() if stderr else 'Unknown error'}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start dnsmasq: {e}")
            return False

    def create_hotspot(self):
        """Create WiFi hotspot"""
        logger.info(f"🏗️ Creating WiFi hotspot: {self.hotspot_ssid}")
        
        # Setup interface
        if not self.setup_interface():
            return False
        
        # Start hostapd
        if not self.start_hostapd():
            return False
        
        # Start dnsmasq
        if not self.start_dnsmasq():
            return False
        
        self.current_mode = "hotspot"
        
        logger.info("🎉 Hotspot created successfully!")
        logger.info(f"📱 Network: {self.hotspot_ssid}")
        logger.info(f"🌐 IP: 192.168.4.1")
        logger.info(f"🌐 Web: http://192.168.4.1")
        
        return True

    def connect_to_wifi(self, ssid, password):
        """Connect to WiFi network"""
        logger.info(f"📡 Connecting to WiFi: {ssid}")
        
        try:
            # Stop hotspot
            self.stop_services()
            
            # Reset interface
            self.run_command(["ip", "addr", "flush", "dev", "wlan0"])
            self.run_command(["ip", "link", "set", "wlan0", "down"])
            time.sleep(1)
            self.run_command(["ip", "link", "set", "wlan0", "up"])
            time.sleep(2)
            
            # Create wpa_supplicant config
            if password:
                wpa_config = f"""network={{
    ssid="{ssid}"
    psk="{password}"
}}"""
            else:
                wpa_config = f"""network={{
    ssid="{ssid}"
    key_mgmt=NONE
}}"""
            
            with open('/tmp/wpa_supplicant.conf', 'w') as f:
                f.write(wpa_config)
            
            # Start wpa_supplicant
            subprocess.Popen([
                "wpa_supplicant", "-B", "-i", "wlan0",
                "-c", "/tmp/wpa_supplicant.conf"
            ])
            
            time.sleep(5)
            
            # Get IP via DHCP
            result = self.run_command(["dhcpcd", "wlan0"], timeout=30)
            time.sleep(5)
            
            # Verify connection
            result = self.run_command(["ip", "addr", "show", "wlan0"])
            if result and "inet " in result.stdout:
                for line in result.stdout.split('\n'):
                    if "inet " in line and "192.168.4." not in line:
                        ip = line.strip().split()[1]
                        logger.info(f"✅ Connected! IP: {ip}")
                        self.save_config(ssid, password)
                        self.current_mode = "client"
                        return True
            
            logger.error("❌ No IP assigned")
            self.create_hotspot()  # Fallback to hotspot
            return False
            
        except Exception as e:
            logger.error(f"WiFi connection failed: {e}")
            self.create_hotspot()  # Fallback to hotspot
            return False

    def save_config(self, ssid, password):
        """Save WiFi configuration"""
        try:
            config = {
                "ssid": ssid,
                "password": password,
                "configured": True,
                "timestamp": time.time()
            }
            os.makedirs("/data", exist_ok=True)
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
            logger.info("Configuration saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def load_config(self):
        """Load WiFi configuration"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
        return None

    def reset_wifi(self):
        """Reset WiFi configuration"""
        logger.info("🔄 Resetting WiFi...")
        
        self.stop_services()
        
        # Remove config
        try:
            if os.path.exists(self.config_file):
                os.remove(self.config_file)
        except Exception as e:
            logger.error(f"Failed to remove config: {e}")
        
        # Reset interface and create hotspot
        return self.create_hotspot()

# Global controller
controller = WiFiController()

# Simple HTML template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>WiFi Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px; background: #f0f2f5; }
        .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #333; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        button { background: #4CAF50; color: white; padding: 15px; width: 100%; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; }
        button:hover { background: #45a049; }
        .status { margin-top: 20px; padding: 15px; border-radius: 5px; text-align: center; }
        .success { background: #d4edda; color: #155724; }
        .error { background: #f8d7da; color: #721c24; }
        .info { margin-top: 20px; padding: 15px; background: #fff3cd; border-radius: 5px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌐 WiFi Setup</h1>
        <form method="POST">
            <input type="text" name="ssid" placeholder="WiFi Network Name (SSID)" required>
            <input type="password" name="password" placeholder="WiFi Password (leave blank for open)">
            <button type="submit">Connect to WiFi</button>
        </form>
        
        {{ status_message }}
        
        <div class="info">
            <strong>Instructions:</strong><br>
            1. Enter your WiFi network details<br>
            2. Click Connect to configure<br>
            3. Device will connect and assign IP to wlan0<br><br>
            <strong>Reset:</strong> Hold button for 5 seconds to reset
        </div>
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        ssid = request.form.get('ssid', '').strip()
        password = request.form.get('password', '').strip()
        
        if not ssid:
            status_msg = '<div class="status error">❌ Please enter network name</div>'
            return render_template_string(HTML_TEMPLATE, status_message=status_msg)
        
        success = controller.connect_to_wifi(ssid, password)
        
        if success:
            return render_template_string("""
            <div class="container">
                <h1>✅ Success!</h1>
                <div class="status success">
                    Connected to {{ ssid }}!<br><br>
                    wlan0 now has an IP address.<br>
                    You can disconnect from this hotspot.
                </div>
            </div>
            """, ssid=ssid)
        else:
            status_msg = f'<div class="status error">❌ Failed to connect to "{ssid}"</div>'
            return render_template_string(HTML_TEMPLATE, status_message=status_msg)
    
    return render_template_string(HTML_TEMPLATE, status_message="")

@app.route('/health')
def health():
    return {"status": "healthy", "mode": controller.current_mode}

@app.route('/reset', methods=['POST'])
def reset():
    success = controller.reset_wifi()
    return {"success": success}

# Captive portal redirects
@app.route('/generate_204')
@app.route('/gen_204')
@app.route('/hotspot-detect.html')
def captive_portal():
    return redirect('http://192.168.4.1', code=302)

def initialize():
    logger.info("🚀 Starting Fixed WiFi Onboarding")
    
    # Check for saved config
    config = controller.load_config()
    if config and config.get('configured'):
        logger.info(f"Found saved WiFi: {config['ssid']}")
        if controller.connect_to_wifi(config['ssid'], config.get('password', '')):
            return True
    
    # Create hotspot
    return controller.create_hotspot()

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}")
    controller.stop_services()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGUSR1, signal_handler)
    
    if not initialize():
        logger.error("Initialization failed")
        sys.exit(1)
    
    try:
        app.run(host="0.0.0.0", port=80, debug=False)
    except Exception as e:
        logger.error(f"Web server failed: {e}")
        sys.exit(1)
# update
