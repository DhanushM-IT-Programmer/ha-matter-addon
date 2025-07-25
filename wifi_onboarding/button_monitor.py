#!/usr/bin/env python3
"""
Simple Button Monitor for Wi-Fi Onboarding Add-on
- GPIO handling with multiple fallback methods
- Simple reset functionality using direct network tools
- No NetworkManager dependency
"""

import time
import subprocess
import sys
import os
import signal
import logging
import threading
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class SimpleButtonMonitor:
    def __init__(self, gpio_pin=17, hold_time=5, debounce_time=0.05):
        self.gpio_pin = gpio_pin
        self.hold_time = hold_time
        self.debounce_time = debounce_time
        self.running = True
        self.button_obj = None
        self.gpio_lib = None
        self.reset_flag = "/tmp/wifi_reset"
        self.config_file = "/data/wifi_config.json"
        self.last_press_time = 0
        self.press_start_time = 0
        self.is_pressed_state = False

    def run_command(self, cmd, timeout=10):
        """Run shell command safely"""
        try:
            logger.debug(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
            return result
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            return None
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return None

    def setup_gpio(self):
        """Initialize GPIO with multiple fallback methods"""
        logger.info(f"Initializing GPIO pin {self.gpio_pin}")

        # Method 1: Try lgpio (RPi 5)
        try:
            import lgpio
            chip = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_input(chip, self.gpio_pin, lgpio.SET_PULL_UP)
            self.button_obj = {'chip': chip, 'pin': self.gpio_pin, 'type': 'lgpio'}
            self.gpio_lib = "lgpio"
            logger.info("Using lgpio library")
            return True
        except Exception as e:
            logger.debug(f"lgpio failed: {str(e)}")

        # Method 2: Try gpiozero
        try:
            os.environ['GPIOZERO_PIN_FACTORY'] = 'native'
            from gpiozero import Button
            button = Button(self.gpio_pin, pull_up=True, bounce_time=self.debounce_time)
            self.button_obj = {'button': button, 'type': 'gpiozero'}
            self.gpio_lib = "gpiozero"
            logger.info("Using gpiozero library")
            return True
        except Exception as e:
            logger.debug(f"gpiozero failed: {str(e)}")

        # Method 3: Try RPi.GPIO
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self.button_obj = {'gpio': GPIO, 'type': 'rpi_gpio'}
            self.gpio_lib = "rpi_gpio"
            logger.info("Using RPi.GPIO library")
            return True
        except Exception as e:
            logger.debug(f"RPi.GPIO failed: {str(e)}")

        # Method 4: Try sysfs GPIO
        try:
            self.setup_sysfs_gpio()
            self.button_obj = {'type': 'sysfs'}
            self.gpio_lib = "sysfs"
            logger.info("Using sysfs GPIO")
            return True
        except Exception as e:
            logger.debug(f"sysfs GPIO failed: {str(e)}")

        logger.error("No working GPIO library found")
        return False

    def setup_sysfs_gpio(self):
        """Setup GPIO using sysfs interface"""
        gpio_base = "/sys/class/gpio"
        gpio_export = f"{gpio_base}/export"
        gpio_dir = f"{gpio_base}/gpio{self.gpio_pin}"
        gpio_direction = f"{gpio_dir}/direction"
        
        # Export GPIO if not already exported
        if not os.path.exists(gpio_dir):
            with open(gpio_export, 'w') as f:
                f.write(str(self.gpio_pin))
        
        # Set direction to input
        with open(gpio_direction, 'w') as f:
            f.write("in")

    def is_button_pressed(self):
        """Check button state"""
        try:
            if not self.button_obj:
                return False
                
            button_type = self.button_obj.get('type')
            
            if button_type == 'lgpio':
                import lgpio
                return lgpio.gpio_read(self.button_obj['chip'], self.button_obj['pin']) == 0
            
            elif button_type == 'gpiozero':
                return self.button_obj['button'].is_pressed
            
            elif button_type == 'rpi_gpio':
                GPIO = self.button_obj['gpio']
                return GPIO.input(self.gpio_pin) == GPIO.LOW
            
            elif button_type == 'sysfs':
                gpio_value = f"/sys/class/gpio/gpio{self.gpio_pin}/value"
                with open(gpio_value, 'r') as f:
                    return f.read().strip() == '0'
            
        except Exception as e:
            logger.error(f"Error reading GPIO: {str(e)}")
        
        return False

    def cleanup_gpio(self):
        """Clean up GPIO resources"""
        try:
            if not self.button_obj:
                return
                
            button_type = self.button_obj.get('type')
            
            if button_type == 'lgpio':
                import lgpio
                lgpio.gpiochip_close(self.button_obj['chip'])
            
            elif button_type == 'gpiozero':
                self.button_obj['button'].close()
            
            elif button_type == 'rpi_gpio':
                GPIO = self.button_obj['gpio']
                GPIO.cleanup()
            
            elif button_type == 'sysfs':
                gpio_unexport = "/sys/class/gpio/unexport"
                try:
                    with open(gpio_unexport, 'w') as f:
                        f.write(str(self.gpio_pin))
                except:
                    pass
            
            self.button_obj = None
            
        except Exception as e:
            logger.debug(f"GPIO cleanup error: {str(e)}")

    def debounce_check(self, current_state):
        """Debounce button state changes"""
        current_time = time.time()
        
        if current_state != self.is_pressed_state:
            if current_time - self.last_press_time >= self.debounce_time:
                self.is_pressed_state = current_state
                self.last_press_time = current_time
                
                if current_state:
                    self.press_start_time = current_time
                    logger.debug("Button press detected")
                else:
                    press_duration = current_time - self.press_start_time
                    logger.debug(f"Button released after {press_duration:.2f}s")
                
                return True
        
        return False

    def reset_wifi_config(self):
        """Reset WiFi configuration"""
        logger.info("🔄 RESET TRIGGERED - Resetting WiFi configuration...")
        
        # Stop network processes
        processes = ["hostapd", "dnsmasq", "wpa_supplicant", "dhcpcd"]
        for process in processes:
            self.run_command(["pkill", "-f", process])
        
        time.sleep(2)
        
        # Remove configuration files
        config_files = [
            self.config_file,
            "/tmp/wifi_onboarding.lock",
            "/tmp/wpa_supplicant.conf",
            "/tmp/hostapd.conf",
            "/tmp/dnsmasq.conf"
        ]
        
        for file_path in config_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Removed {file_path}")
            except Exception as e:
                logger.error(f"Failed to remove {file_path}: {str(e)}")

        # Reset wlan0 interface
        try:
            # Try to unblock WiFi if rfkill is available
            result = self.run_command(["which", "rfkill"])
            if result and result.returncode == 0:
                self.run_command(["rfkill", "unblock", "wifi"])
                time.sleep(1)
            
            # Reset interface
            self.run_command(["ip", "addr", "flush", "dev", "wlan0"])
            self.run_command(["ip", "link", "set", "wlan0", "down"])
            time.sleep(1)
            self.run_command(["ip", "link", "set", "wlan0", "up"])
            time.sleep(1)
            
            logger.info("Reset wlan0 interface")
        except Exception as e:
            logger.warning(f"Failed to reset wlan0: {e}")

        # Create reset flag
        try:
            Path(self.reset_flag).touch()
            logger.info(f"Created reset flag at {self.reset_flag}")
        except Exception as e:
            logger.error(f"Failed to create reset flag: {str(e)}")

        # Signal main process
        try:
            self.run_command(["pkill", "-USR1", "-f", "onboarding.py"])
            logger.info("Signaled main process for restart")
        except Exception as e:
            logger.warning(f"Failed to signal main process: {e}")

        logger.info("✅ Reset sequence completed")

    def monitor_button(self):
        """Main button monitoring loop"""
        if not self.setup_gpio():
            logger.warning("Button monitoring disabled - GPIO setup failed")
            try:
                while self.running:
                    time.sleep(60)
            except KeyboardInterrupt:
                pass
            return

        logger.info(f"🎛️ Monitoring GPIO {self.gpio_pin}, hold for {self.hold_time}s to reset")
        
        hold_counter = 0
        feedback_given = False
        
        try:
            while self.running:
                current_pressed = self.is_button_pressed()
                
                if self.debounce_check(current_pressed):
                    if current_pressed:
                        hold_counter = 0
                        feedback_given = False
                        logger.info("🔘 Button pressed - hold for reset")
                    else:
                        if hold_counter > 0:
                            logger.info(f"Button released after {hold_counter}s")
                        hold_counter = 0
                        feedback_given = False
                
                if self.is_pressed_state:
                    hold_counter += 1
                    
                    if hold_counter == 3 and not feedback_given:
                        logger.info("⏳ Keep holding for reset...")
                        feedback_given = True
                    
                    elif hold_counter >= self.hold_time:
                        logger.info(f"🚨 LONG PRESS DETECTED ({self.hold_time}s) - TRIGGERING RESET!")
                        self.reset_wifi_config()
                        hold_counter = 0
                        feedback_given = False
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Button monitor interrupted")
        except Exception as e:
            logger.error(f"Button monitor error: {str(e)}")
        finally:
            self.cleanup_gpio()

def main():
    """Entry point"""
    import argparse
    parser = argparse.ArgumentParser(description='WiFi Onboarding Button Monitor')
    parser.add_argument('--pin', type=int, default=17, help='GPIO pin number (default: 17)')
    parser.add_argument('--hold', type=int, default=5, help='Hold time in seconds (default: 5)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    monitor = SimpleButtonMonitor(gpio_pin=args.pin, hold_time=args.hold)

    def handle_signal(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        monitor.running = False
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("🚀 Starting Button Monitor")
    monitor.monitor_button()
    logger.info("Button monitor shutdown complete")

if __name__ == "__main__":
    main()
# update
