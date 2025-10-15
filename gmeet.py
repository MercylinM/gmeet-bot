import asyncio
import subprocess
import os
import json
import time
import click
import datetime
import requests
import websockets
import threading
from time import sleep
import re
import sys
from flask import Flask, request, jsonify
from flask_cors import CORS
from queue import Queue, Empty

import sounddevice as sd
import numpy as np

import undetected_chromedriver as uc
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Configuration
class Config:
    # Audio settings
    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = 'int16'
    BLOCKSIZE = 2048
    LATENCY = 'low'
    
    # WebSocket settings
    WS_PING_INTERVAL = 20
    WS_PING_TIMEOUT = 10
    WS_CLOSE_TIMEOUT = 5
    
    # Browser settings
    CHROME_VERSION = 108  # Fallback version
    WINDOW_SIZE = "1280x720"
    
    # Health check settings
    HEALTH_CHECK_INTERVAL = 600  # seconds

app = Flask(__name__)
CORS(app)

class AudioDeviceManager:
    """Manage audio device discovery and configuration"""
    
    @staticmethod
    def list_audio_devices():
        """List all available audio input devices"""
        try:
            devices = sd.query_devices()
            input_devices = []
            
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    input_devices.append({
                        'index': i,
                        'name': device['name'],
                        'channels': device['max_input_channels'],
                        'sample_rate': device['default_samplerate']
                    })
            
            return input_devices
        except Exception as e:
            print(f"Error listing audio devices: {e}")
            return []
    
    @staticmethod
    def find_best_device():
        """Find the best audio input device for container environment"""
        try:
            devices = AudioDeviceManager.list_audio_devices()
            
            if not devices:
                print("❌ No audio input devices found")
                return None
            
            # In container environments, use simpler approach
            print("🔍 Available audio devices:")
            for device in devices:
                print(f"  - Device {device['index']}: {device['name']} ({device['channels']} channels)")
            
            # Try to use the default device first
            try:
                default_device = sd.default.device
                if isinstance(default_device, tuple):
                    default_input = default_device[0]  # Input device is first
                else:
                    default_input = default_device
                
                print(f"🎯 Using default input device: {default_input}")
                return default_input
            except Exception as e:
                print(f"⚠️ Cannot use default device: {e}")
            
            # Fallback: use any available input device
            for device in devices:
                try:
                    print(f"🔄 Using device {device['index']}: {device['name']}")
                    return device['index']
                except:
                    continue
            
            print("❌ No accessible audio input devices found")
            return None
            
        except Exception as e:
            print(f"❌ Error finding audio device: {e}")
            return None

class RealtimeAudioStreamer:
    def __init__(self, backend_url):
        self.backend_url = backend_url
        self.ws_url = backend_url.replace('http', 'ws') + '/ws/audio'
        self.websocket = None
        self.is_streaming = False
        self.stream = None
        self.bytes_transmitted = 0
        self.last_activity_time = datetime.datetime.now()
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5
        self.audio_queue = Queue(maxsize=100)
        self._stop_event = threading.Event()
        
        # Audio configuration
        self.sample_rate = Config.SAMPLE_RATE
        self.channels = Config.CHANNELS
        self.dtype = Config.DTYPE
        self.blocksize = Config.BLOCKSIZE
        self.device_index = None
        
    async def connect_websocket(self):
        """Connect to backend WebSocket for audio streaming"""
        try:
            print(f"Connecting to audio WebSocket: {self.ws_url}")
            
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=Config.WS_PING_INTERVAL,
                ping_timeout=Config.WS_PING_TIMEOUT,
                close_timeout=Config.WS_CLOSE_TIMEOUT,
                max_size=None,
            )
            
            print("✅ Connected to audio WebSocket")
            self.is_connected = True
            self.reconnect_attempts = 0
            return True
            
        except Exception as e:
            print(f"❌ WebSocket connection failed: {e}")
            self.is_connected = False
            self.reconnect_attempts += 1
            return False

    def _is_websocket_open(self):
        """Check if WebSocket connection is open"""
        if not self.websocket:
            return False
        
        try:
            if hasattr(self.websocket, 'state'):
                return self.websocket.state == websockets.protocol.State.OPEN
            elif hasattr(self.websocket, 'closed'):
                return not self.websocket.closed
            else:
                return True
        except Exception:
            return False

    def _audio_callback(self, indata, frames, time, status):
        """Callback function for sounddevice stream"""
        if status:
            print(f"Audio callback status: {status}")
        
        if self.is_streaming and not self._stop_event.is_set():
            try:
                # Convert numpy array to bytes
                audio_data = indata.tobytes()
                
                # Non-blocking put to avoid backpressure
                if not self.audio_queue.full():
                    self.audio_queue.put(audio_data, block=False)
                    self.bytes_transmitted += len(audio_data)
                    self.last_activity_time = datetime.datetime.now()
                
            except Exception as e:
                print(f"Error in audio callback: {e}")

    def start_realtime_streaming(self, duration_minutes=60):
        """Start real-time audio streaming to backend"""
        if self.is_streaming:
            print("⚠️ Audio streaming already running")
            return None

        print("🎵 Starting real-time audio streaming...")
        self.is_streaming = True
        self._stop_event.clear()
        
        # Find and configure audio device
        self.device_index = AudioDeviceManager.find_best_device()
        
        if self.device_index is None:
            print("❌ No suitable audio input device found")
            self.is_streaming = False
            return None

        # Start audio capture thread
        capture_thread = threading.Thread(
            target=self._capture_audio,
            daemon=True,
            name="AudioCaptureThread"
        )
        capture_thread.start()
        
        # Start WebSocket sender thread
        sender_thread = threading.Thread(
            target=self._run_websocket_sender,
            daemon=True,
            name="WebSocketSenderThread"
        )
        sender_thread.start()
        
        print("✅ Audio streaming started successfully")
        return [capture_thread, sender_thread]

    def _capture_audio(self):
        """Capture audio using sounddevice"""
        print("🎤 Starting audio capture with sounddevice...")
        
        try:
            # Configure stream parameters
            stream_params = {
                'samplerate': self.sample_rate,
                'channels': self.channels,
                'dtype': self.dtype,
                'blocksize': self.blocksize,
                'callback': self._audio_callback,
                'latency': Config.LATENCY
            }
            
            if self.device_index is not None:
                stream_params['device'] = self.device_index
            
            # Start the audio stream
            self.stream = sd.InputStream(**stream_params)
            self.stream.start()
            
            print(f"✅ Audio stream started: {self.sample_rate}Hz, {self.channels} channel, {self.dtype}, device: {self.device_index}")

            # Keep the thread alive while streaming
            while self.is_streaming and not self._stop_event.is_set() and self.stream.active:
                time.sleep(0.1)
                
                # Periodic stats logging
                if self.bytes_transmitted > 0 and self.bytes_transmitted % (500 * 1024) < self.blocksize * 4:
                    kb_transmitted = self.bytes_transmitted / 1024
                    queue_size = self.audio_queue.qsize()
                    print(f"📊 Audio stats: {kb_transmitted:.2f} KB captured, queue: {queue_size}")

        except Exception as e:
            print(f"❌ Audio capture error: {e}")
        finally:
            self._cleanup_audio_capture()

    def _run_websocket_sender(self):
        """Run WebSocket sender in a separate event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._websocket_sender_async())
        except Exception as e:
            print(f"❌ WebSocket sender error: {e}")
        finally:
            loop.close()

    async def _websocket_sender_async(self):
        """Async WebSocket sender that reads from queue and sends to server"""
        print("🌐 Starting WebSocket sender...")
        
        if not await self.connect_websocket():
            print("❌ Failed initial WebSocket connection")
            self.is_streaming = False
            return

        last_stats_time = datetime.datetime.now()
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        while self.is_streaming and not self._stop_event.is_set():
            try:
                # Get audio data from queue with timeout
                try:
                    audio_data = self.audio_queue.get(timeout=1.0)
                except Empty:
                    continue

                # Send data if WebSocket is connected
                if self._is_websocket_open():
                    try:
                        await self.websocket.send(audio_data)
                        consecutive_failures = 0  # Reset failure counter on success
                        
                        # Log stats every 30 seconds
                        current_time = datetime.datetime.now()
                        if (current_time - last_stats_time).total_seconds() >= 30:
                            kb_transmitted = self.bytes_transmitted / 1024
                            queue_size = self.audio_queue.qsize()
                            print(f"📈 Streaming stats: {kb_transmitted:.2f} KB sent, queue: {queue_size}")
                            last_stats_time = current_time
                            
                    except (websockets.exceptions.ConnectionClosed, 
                           websockets.exceptions.WebSocketException) as e:
                        print(f"🔌 WebSocket send error: {e}")
                        self.is_connected = False
                        consecutive_failures += 1
                        
                        if consecutive_failures >= max_consecutive_failures:
                            print("❌ Too many consecutive failures, stopping stream")
                            break
                            
                        if not await self._reconnect_websocket():
                            print("❌ Failed to reconnect WebSocket")
                            break
                else:
                    print("⚠️ WebSocket not connected, attempting reconnect...")
                    if not await self._reconnect_websocket():
                        print("❌ Failed to reconnect WebSocket")
                        break
                
                self.audio_queue.task_done()
                
            except Exception as e:
                print(f"❌ WebSocket sender error: {e}")
                await asyncio.sleep(0.1)

        print("🛑 WebSocket sender stopped")

    async def _reconnect_websocket(self):
        """Attempt to reconnect WebSocket with backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            print("❌ Max reconnection attempts reached")
            return False

        delay = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 60)
        print(f"🔄 Attempting reconnect in {delay}s (attempt {self.reconnect_attempts + 1})")
        
        await asyncio.sleep(delay)
        
        if await self.connect_websocket():
            print("✅ WebSocket reconnected successfully")
            # Clear queue on reconnect to avoid sending old data
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                    self.audio_queue.task_done()
                except Empty:
                    break
            return True
        else:
            return False

    def _cleanup_audio_capture(self):
        """Clean up audio capture resources"""
        if self.stream:
            print("🛑 Stopping audio stream...")
            try:
                self.stream.stop()
                self.stream.close()
                print("✅ Audio stream stopped successfully")
            except Exception as e:
                print(f"❌ Error stopping audio stream: {e}")
            finally:
                self.stream = None

    async def cleanup(self):
        """Clean up all streaming resources"""
        print("🧹 Cleaning up audio streamer...")
        self.is_streaming = False
        self._stop_event.set()
        self.is_connected = False
        
        self._cleanup_audio_capture()
        
        # Clear audio queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Empty:
                break
        
        # Close WebSocket connection
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.close()
                print("✅ Audio WebSocket connection closed")
            except Exception as e:
                print(f"❌ Error closing WebSocket: {e}")
            self.websocket = None
        
        print(f"📊 Final stats: {self.bytes_transmitted / 1024:.2f} KB transmitted total")

    def stop_streaming(self):
        """Stop streaming synchronously"""
        print("🛑 Stopping audio streaming...")
        self.is_streaming = False
        self._stop_event.set()
        
    def get_status(self):
        """Get current streaming status"""
        return {
            'is_streaming': self.is_streaming,
            'is_connected': self.is_connected,
            'bytes_transmitted': self.bytes_transmitted,
            'queue_size': self.audio_queue.qsize(),
            'reconnect_attempts': self.reconnect_attempts,
            'device_index': self.device_index
        }

# Bot state management
bot_state = {
    'status': 'idle', 
    'current_meeting': None,
    'start_time': None,
    'thread': None,
    'driver': None,  
    'audio_streamer': None,
    'last_health_check': datetime.datetime.now()
}

def keep_alive():
    """Send periodic requests to keep the service alive"""
    while True:
        try:
            bot_state['last_health_check'] = datetime.datetime.now()

            health_url = "https://gmeet-bot.onrender.com/health"
            response = requests.get(health_url, timeout=30)
            if response.status_code == 200:
                print("✅ Keep-alive ping successful")
            else:
                print(f"⚠️ Keep-alive ping failed with status: {response.status_code}")

            time.sleep(Config.HEALTH_CHECK_INTERVAL)
        except Exception as e:
            print(f"❌ Keep-alive error: {e}")
            time.sleep(60)  

keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
keep_alive_thread.start()

# Flask Routes
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    audio_status = bot_state['audio_streamer'].get_status() if bot_state['audio_streamer'] else {}
    
    return jsonify({
        'status': 'healthy',
        'service': 'gmeet-bot',
        'bot_status': bot_state['status'],
        'current_meeting': bot_state['current_meeting'],
        'audio_status': audio_status,
        'uptime': (datetime.datetime.now() - bot_state['start_time']).total_seconds() if bot_state['start_time'] else 0,
        'timestamp': datetime.datetime.now().isoformat()
    }), 200

@app.route('/audio-devices', methods=['GET'])
def list_audio_devices():
    """List available audio input devices"""
    try:
        devices = AudioDeviceManager.list_audio_devices()
        return jsonify({
            'success': True,
            'devices': devices,
            'count': len(devices)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/start', methods=['POST'])
def start_bot():
    """Start the bot with provided meeting details"""
    try:
        if bot_state['status'] in ['running', 'starting']:
            return jsonify({
                'success': False,
                'error': 'Bot is already running',
                'status': bot_state['status']
            }), 400

        data = request.json
        meet_link = data.get('meet_link') or data.get('meetLink')
        duration = data.get('duration', 60)

        if not meet_link:
            return jsonify({
                'success': False,
                'error': 'Meeting link is required'
            }), 400

        # Validate audio devices are available
        devices = AudioDeviceManager.list_audio_devices()
        if not devices:
            return jsonify({
                'success': False,
                'error': 'No audio input devices available'
            }), 400

        print(f"🎯 Starting bot for meeting: {meet_link}")
        print(f"📅 Duration: {duration} minutes")
        print(f"🎵 Available audio devices: {len(devices)}")

        os.environ['GMEET_LINK'] = meet_link
        os.environ['DURATION_IN_MINUTES'] = str(duration)

        bot_state['status'] = 'starting'
        bot_state['current_meeting'] = meet_link
        bot_state['start_time'] = datetime.datetime.now()

        def run_bot():
            try:
                asyncio.run(join_meet())
            except Exception as e:
                print(f"❌ Error in bot thread: {e}")
                bot_state['status'] = 'error'
            finally:
                cleanup_bot()

        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()
        bot_state['thread'] = thread

        return jsonify({
            'success': True,
            'status': 'starting',
            'meet_link': meet_link,
            'duration': duration,
            'audio_devices_available': len(devices),
            'message': 'Bot is starting and will join the meeting shortly'
        })

    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        bot_state['status'] = 'error'
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/stop', methods=['POST'])
def stop_bot():
    """Stop the bot"""
    try:
        if bot_state['status'] == 'idle':
            return jsonify({
                'success': True,
                'message': 'Bot is not running'
            })

        print("🛑 Stop signal received, cleaning up bot...")
        bot_state['status'] = 'stopping'
        
        cleanup_bot()
        
        return jsonify({
            'success': True,
            'message': 'Bot stopped successfully'
        })

    except Exception as e:
        print(f"❌ Error stopping bot: {e}")
        bot_state['status'] = 'error'
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def cleanup_bot():
    """Cleanup bot resources - stop audio, quit driver"""
    print("🧹 Cleaning up bot resources...")
    
    # Stop audio streaming
    if bot_state['audio_streamer']:
        try:
            bot_state['audio_streamer'].stop_streaming()
            print("✅ Audio streamer stopped")
        except Exception as e:
            print(f"❌ Error stopping audio streamer: {e}")
    
    # Quit browser driver
    if bot_state['driver']:
        try:
            bot_state['driver'].quit()
            print("✅ Chrome driver quit")
        except Exception as e:
            print(f"❌ Error quitting driver: {e}")
    
    # Reset bot state
    bot_state['status'] = 'idle'
    bot_state['current_meeting'] = None
    bot_state['driver'] = None
    bot_state['audio_streamer'] = None
    
    print("✅ Bot cleanup complete")

@app.route('/status', methods=['GET'])
def get_status():
    """Get current bot status"""
    audio_status = bot_state['audio_streamer'].get_status() if bot_state['audio_streamer'] else {}
    
    return jsonify({
        'success': True,
        'status': bot_state['status'],
        'isRunning': bot_state['status'] == 'running',
        'current_meeting': bot_state['current_meeting'],
        'audio_status': audio_status,
        'uptime': (datetime.datetime.now() - bot_state['start_time']).total_seconds() if bot_state['start_time'] else 0
    })

@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API information"""
    return jsonify({
        'service': 'Google Meet Bot with SoundDevice',
        'version': '2.0.0',
        'audio_backend': 'sounddevice',
        'endpoints': {
            'health': 'GET /health',
            'audio_devices': 'GET /audio-devices',
            'start': 'POST /start',
            'stop': 'POST /stop',
            'status': 'GET /status'
        }
    })

# Browser automation functions - USING YOUR PROVEN METHODS
async def google_sign_in(email, password, driver):
    print("🔐 Starting Google sign in process")
    
    try:
        # Navigate to Google sign in page
        print("🔗 Navigating to Google sign in page")
        driver.get("https://accounts.google.com")
        
        # Wait for page to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "identifier"))
        )
        
        # Enter email
        print("📧 Entering email")
        email_field = driver.find_element(By.NAME, "identifier")
        email_field.clear()
        email_field.send_keys(email)
        
        # Click next
        print("➡️ Clicking next button")
        driver.find_element(By.ID, "identifierNext").click()
        
        # Wait for password field
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "Passwd"))
        )
        
        # Enter password
        print("🔑 Entering password")
        password_field = driver.find_element(By.NAME, "Passwd")
        password_field.clear()
        password_field.send_keys(password)
        password_field.send_keys(Keys.RETURN)
        
        # Wait for sign in to complete
        print("⏳ Waiting for sign in to complete")
        WebDriverWait(driver, 15).until(
            lambda driver: driver.current_url and "accounts.google.com" not in driver.current_url
        )
        
        print("✅ Google sign in completed successfully")
        
    except TimeoutException as e:
        print(f"❌ Timeout during Google sign in: {e}")
        # Take screenshot for debugging
        try:
            driver.save_screenshot("screenshots/signin_error.png")
            print("📸 Saved screenshot to screenshots/signin_error.png")
        except:
            pass
        raise e
    except Exception as e:
        print(f"❌ Error during Google sign in: {e}")
        raise e

def get_chrome_version():
    """Try to detect the installed Chrome version"""
    try:
        if os.name == 'nt':  
            cmd = 'reg query "HKEY_CURRENT_USER\\Software\\Google\\Chrome\\BLBeacon" /v version'
        else:  
            cmd = 'google-chrome --version || chromium-browser --version || chromium --version'
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                version_str = match.group(1)
                major_version = int(version_str.split('.')[0])
                return major_version
    except Exception as e:
        print(f"Error detecting Chrome version: {e}")
    
    return 108

def cleanup_chrome_processes():
    """Clean up any existing Chrome processes"""
    try:
        if os.name == 'nt':  
            subprocess.run("taskkill /f /im chrome.exe /t", shell=True)
            subprocess.run("taskkill /f /im chromedriver.exe /t", shell=True)
        else:  
            subprocess.run("pkill -f chrome", shell=True)
            subprocess.run("pkill -f chromedriver", shell=True)
        print("Cleaned up existing Chrome processes")
    except Exception as e:
        print(f"Error cleaning up Chrome processes: {e}")

async def join_meet():
    print("🔧 Step 1: Starting join_meet function")
    bot_state['status'] = 'running'

    try:
        print("🔧 Step 2: Cleaning up Chrome processes")
        cleanup_chrome_processes()

        meet_link = os.getenv("GMEET_LINK", "https://meet.google.com/mhj-bcdx-bgu")
        backend_url = os.getenv("BACKEND_URL", "https://add-on-backend.onrender.com")
        
        print(f"🔗 Starting recorder for {meet_link}")
        print(f"🌐 Using backend: {backend_url}")

        if bot_state['status'] == 'stopping':
            print("Stop signal received before starting, aborting")
            cleanup_bot()
            return

        # Check backend health
        try:
            health_response = requests.get(f"{backend_url}/health", timeout=5)
            if health_response.ok:
                print(f"✅ Backend is healthy: {health_response.json()}")
            else:
                print(f"⚠️ Backend health check failed: {health_response.status_code}")
        except Exception as e:
            print(f"❌ Cannot connect to backend: {e}")

        # Initialize Chrome driver
        print("🔧 Step 3: Initializing Chrome driver")
        driver = None

        try:
            chrome_version = get_chrome_version()
            print(f"🔍 Detected Chrome version: {chrome_version}")
            
            options = uc.ChromeOptions()
            options.add_argument("--use-fake-ui-for-media-stream")
            options.add_argument("--window-size=1280x720")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-setuid-sandbox")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-application-cache")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-features=VizDisplayCompositor")
            options.add_argument("--disable-features=TranslateUI")
            options.add_argument("--disable-ipc-flooding-protection")
            options.add_argument("--disable-background-timer-throttling")
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--disable-backgrounding-occluded-windows")
            options.add_argument("--disable-features=AudioServiceOutOfProcess")
            options.add_argument("--remote-debugging-port=9222")
            options.add_argument("--autoplay-policy=no-user-gesture-required") 
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-default-apps")
            options.add_argument("--disable-sync")
            options.add_argument("--metrics-recording-only")
            options.add_argument("--disable-password-generation")
            options.add_argument("--disable-translate")
            options.add_argument("--disable-features=AutofillServerCommunication")
            
            log_path = "chromedriver.log"
            
            driver = uc.Chrome(
                version_main=chrome_version,
                service_log_path=log_path, 
                use_subprocess=False, 
                options=options
            )
            
            print("✅ Chrome driver initialized successfully")
        except Exception as e:
            print(f"❌ Error initializing Chrome driver: {e}")
            
            try:
                print("🔄 Trying fallback Chrome driver...")
                fallback_options = uc.ChromeOptions()
                fallback_options.add_argument("--use-fake-ui-for-media-stream")
                fallback_options.add_argument("--window-size=1920x1080")
                fallback_options.add_argument("--no-sandbox")
                fallback_options.add_argument("--disable-setuid-sandbox")
                fallback_options.add_argument("--disable-gpu")
                
                driver = uc.Chrome(
                    version_main=108,
                    service_log_path=log_path, 
                    use_subprocess=False, 
                    options=fallback_options
                )
                print("✅ Fallback Chrome driver initialized")
            except Exception as e2:
                print(f"❌ Error with fallback Chrome driver: {e2}")
                bot_state['status'] = 'error'
                cleanup_bot()
                return
            
            if not driver:
                print("❌ Failed to initialize Chrome driver")
                bot_state['status'] = 'error'
                cleanup_bot()
                return
        
        bot_state['driver'] = driver
        driver.set_window_size(1280, 720)

        # Check credentials
        email = os.getenv("GMAIL_USER_EMAIL", "")
        password = os.getenv("GMAIL_USER_PASSWORD", "")

        if email == "" or password == "":
            print("❌ Error: No email or password specified")
            driver.quit()
            bot_state['status'] = 'error'
            cleanup_bot()
            return

        print("🔐 Step 4: Google Sign in")
        try:
            await google_sign_in(email, password, driver)
            print("✅ Google sign in completed")
        except Exception as e:
            print(f"❌ Error during Google sign in: {e}")
            driver.quit()
            bot_state['status'] = 'error'
            cleanup_bot()
            return

        if bot_state['status'] == 'stopping':
            print("Stop signal received, cleaning up")
            cleanup_bot()
            return

        print("🔗 Step 5: Navigating to meet link")
        try:
            driver.get(meet_link)
            print(f"✅ Navigated to {meet_link}")
            sleep(3)
        except Exception as e:
            print(f"❌ Error navigating to meet link: {e}")
            driver.quit()
            bot_state['status'] = 'error'
            cleanup_bot()
            return

        # Grant permissions
        try:
            print("🔐 Step 6: Granting permissions")
            driver.execute_cdp_cmd(
                "Browser.grantPermissions",
                {
                    "origin": meet_link,
                    "permissions": [
                        "geolocation",
                        "audioCapture",
                        "displayCapture",
                        "videoCapture"
                    ],
                },
            )
            print("✅ Permissions granted")
        except Exception as e:
            print(f"⚠️ Warning: Could not grant permissions: {e}")

        if bot_state['status'] == 'stopping':
            print("Stop signal received, cleaning up")
            cleanup_bot()
            return

        # Handle popups
        try:
            print("🔧 Step 7: Handling popups")
            driver.find_element(
                By.XPATH,
                "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
            ).click()
            sleep(1)
            print("✅ Popup handled")
        except:
            print("ℹ️ No popup found")

        # Handle microphone
        print("🎤 Step 8: Handling microphone settings")
        sleep(3)

        missing_mic = False

        try:
            print("Checking for missing mic popup")
            driver.find_element(By.CLASS_NAME, "VfPpkd-vQzf8d").find_element(By.XPATH, "..")
            sleep(1)
            missing_mic = True
            print("ℹ️ Missing mic popup detected")
        except:
            print("ℹ️ No missing mic popup")

        try:
            print("Allowing microphone")
            driver.find_element(
                By.XPATH,
                "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
            ).click()
            sleep(1)
            print("✅ Microphone allowed")
        except:
            print("ℹ️ No microphone permission popup")

        try:
            print("Disabling microphone")
            driver.find_element(
                By.XPATH,
                '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[1]/div[1]/div/div[6]/div[1]/div/div',
            ).click()
            print("✅ Microphone disabled")
        except:
            print("ℹ️ No microphone to disable")

        sleep(1)

        # Handle camera
        print("📷 Step 9: Handling camera settings")
        if not missing_mic:
            try:
                driver.find_element(
                    By.XPATH,
                    '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[1]/div[1]/div/div[6]/div[2]/div',
                ).click()
                print("✅ Camera disabled")
            except:
                print("ℹ️ No camera to disable")
        else:
            print("ℹ️ Assuming missing mic = missing camera")
        
        # Set name
        print("👤 Step 10: Setting bot name")
        try:
            name_input_selectors = [
                '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[1]/div[3]/label/input',
                '//input[@type="text"]',
                '//input[contains(@placeholder, "Your name")]',
                '//input[contains(@aria-label, "Your name")]'
            ]
            
            name_set = False
            for selector in name_input_selectors:
                try:
                    name_input = driver.find_element(By.XPATH, selector)
                    name_input.click()
                    sleep(1)
                    name_input.send_keys("Recos AI Bot")
                    sleep(1)
                    name_set = True
                    print(f"✅ Name set using selector: {selector}")
                    break
                except:
                    continue
            
            if not name_set:
                print("⚠️ Could not set name")
        except Exception as e:
            print(f"❌ Error setting name: {e}")

        if bot_state['status'] == 'stopping':
            print("Stop signal received, cleaning up")
            cleanup_bot()
            return

        # Join meeting
        print("🔗 Step 11: Joining meeting")
        try:
            print("Looking for join button...")
            wait = WebDriverWait(driver, 5)
            
            join_button_selectors = [
                "//span[contains(text(), 'Ask to join')]",
                "//span[contains(text(), 'Join now')]",
                "//span[contains(text(), 'Switch here')]",
                "//span[contains(text(), 'Join')]",
                "//span[contains(text(), 'Continue')]",
                "//span[contains(text(), 'Request to join')]",
                "//button[contains(text(), 'Ask to join')]",
                "//button[contains(text(), 'Join now')]",
                "//button[contains(text(), 'Request to join')]",
                "//button[contains(text(), 'Join')]",
                "//button[contains(text(), 'Continue')]",
                "//button[contains(@aria-label, 'Join now')]",
                "//button[contains(@aria-label, 'Ask to join')]",
                "//button[contains(@aria-label, 'Join')]",
                "//button[contains(@aria-label, 'Continue')]",
                "//button[contains(@data-tooltip, 'Ask to join')]",
                "//button[contains(@data-tooltip, 'Join now')]",
                "//button[contains(@data-tooltip, 'Join')]",
                "//button[contains(@data-tooltip, 'Continue')]",
                "//div[contains(text(), 'Ask to join')]",
                "//div[contains(text(), 'Request to join')]",
                "//div[contains(text(), 'Join now')]",
                "//div[contains(text(), 'Join')]",
                "//div[contains(text(), 'Continue')]",
                "//div[contains(@aria-label, 'Ask to join')]",
                "//div[contains(@aria-label, 'Join now')]",
                "//div[contains(@aria-label, 'Join')]",
                "//div[contains(@aria-label, 'Continue')]",
                "//div[contains(@data-tooltip, 'Ask to join')]",
                "//div[contains(@data-tooltip, 'Join now')]",
                "//div[contains(@data-tooltip, 'Join')]",
                "//div[contains(@data-tooltip, 'Continue')]"
            ]
            
            joined = False
            for selector in join_button_selectors:
                try:
                    join_button = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                    join_button.click()
                    print(f"✅ Clicked join button using selector: {selector}")
                    joined = True
                    break
                except TimeoutException:
                    continue
            
            if not joined:
                print("⚠️ Could not find any join button")
        except Exception as e:
            print(f"❌ Error handling join button: {e}")

        if bot_state['status'] == 'stopping':
            print("Stop signal received, cleaning up")
            cleanup_bot()
            return

        # Wait for meeting to load
        print("⏳ Step 12: Waiting for meeting to load...")
        sleep(10)

        # Check if in meeting
        try:
            print("🔍 Checking if in meeting")
            wait = WebDriverWait(driver, 5)
            
            meeting_indicators = [
                "//div[contains(@data-self-name, 'Recos AI Bot')]",
                "//span[contains(text(), 'You')]",
                "//div[contains(@aria-label, 'You are')]",
                "//button[contains(@aria-label, 'Leave call')]",
                "//button[contains(@data-tooltip, 'Leave call')]",
                "//div[contains(text(), 'Meeting details')]",
                "//div[contains(text(), 'People')]",
                "//div[contains(text(), 'Chat')]",
                "//div[contains(text(), 'Activities')]"
            ]
            
            in_meeting = False
            for selector in meeting_indicators:
                try:
                    wait.until(EC.presence_of_element_located((By.XPATH, selector)))
                    in_meeting = True
                    print(f"✅ Detected meeting using selector: {selector}")
                    break
                except TimeoutException:
                    continue
            
            if in_meeting:
                print("✅ Successfully joined the meeting!")
            else:
                print("⚠️ Could not confirm if in meeting, proceeding anyway...")
        except Exception as e:
            print(f"❌ Error checking meeting status: {e}")

        if bot_state['status'] == 'stopping':
            print("Stop signal received, cleaning up")
            cleanup_bot()
            return

        # Initialize audio streamer
        print("🎵 Step 13: Initializing audio streamer")
        audio_streamer = RealtimeAudioStreamer(backend_url)
        bot_state['audio_streamer'] = audio_streamer

        # Start recording
        print("🎙️ Step 14: Starting recording and streaming")
        duration_minutes = int(os.getenv("DURATION_IN_MINUTES", "60"))  
        duration_seconds = duration_minutes * 60

        print(f"📅 Duration: {duration_minutes} minutes")
        
        streaming_thread = audio_streamer.start_realtime_streaming(duration_minutes)
        print(f"🎙️ Recording for {duration_minutes} minutes...")

        # Monitor recording
        print("⏱️ Step 15: Monitoring recording...")
        elapsed = 0
        last_status_check = 0
        status_check_interval = 60  
        
        while elapsed < duration_seconds and bot_state['status'] != 'stopping':
            await asyncio.sleep(1)
            elapsed += 1
            
            if elapsed - last_status_check >= status_check_interval:
                if elapsed == 30 and audio_streamer.bytes_transmitted == 0:
                    print("⚠️ WARNING: No audio data transmitted after 30 seconds!")
                
                if not audio_streamer.is_connected:
                    print(f"⚠️ WARNING: WebSocket disconnected at {elapsed} seconds")
                    
                print(f"📊 Status check at {elapsed}s: Connected={audio_streamer.is_connected}, "
                      f"Bytes sent={audio_streamer.bytes_transmitted/1024:.2f}KB")
                last_status_check = elapsed
        
        # Cleanup
        print("🧹 Step 16: Cleaning up session...")
        if streaming_thread:
            for thread in streaming_thread:
                if thread.is_alive():
                    thread.join(timeout=10)

        if driver:
            try:
                driver.quit()
                print("✅ Chrome driver quit")
            except Exception as e:
                print(f"❌ Error quitting driver: {e}")
        
        bot_state['status'] = 'idle'
        bot_state['driver'] = None
        bot_state['audio_streamer'] = None
        bot_state['current_meeting'] = None
        
        print("✅ Bot session ended cleanly")

    except Exception as e:
        print(f"❌ Unhandled error in join_meet: {e}")
        import traceback
        traceback.print_exc()
        cleanup_bot()

def run_flask_server():
    """Run Flask server in the main thread"""
    port = int(os.getenv('PORT', 10000))
    print(f"Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_production_server():
    """Run production server with better configuration for Render"""
    port = int(os.getenv('PORT', 10000))
    
    try:
        import gunicorn.app.base
        
        class GunicornApp(gunicorn.app.base.BaseApplication):
            def __init__(self, app, options=None):
                self.options = options or {}
                self.application = app
                super().__init__()

            def load_config(self):
                for key, value in self.options.items():
                    self.cfg.set(key.lower(), value)

            def load(self):
                return self.application
        
        options = {
            'bind': f'0.0.0.0:{port}',
            'workers': 1,  
            'timeout': 120,
            'accesslog': '-',
            'errorlog': '-',
            'keepalive': 5,
            'max_requests': 1000,
            'max_requests_jitter': 100,
            'preload_app': True
        }
        
        print(f"Starting production server on port {port}")
        GunicornApp(app, options).run()
        
    except ImportError:
        print("Gunicorn not available, falling back to Flask development server")
        app.run(host='0.0.0.0', port=port, debug=False)

@click.command()
@click.option('--meet-link', help='Google Meet link')
@click.option('--duration', default=60, help='Duration in minutes')
@click.option('--server', is_flag=True, help='Run as HTTP server')
@click.option('--production', is_flag=True, help='Run in production mode')
def main(meet_link, duration, server, production):
    if server or os.getenv('RUN_AS_SERVER', 'true').lower() == 'true':
        if production or os.getenv('FLASK_ENV') == 'production':
            run_production_server()
        else:
            run_flask_server()
    else:
        if meet_link:
            os.environ["GMEET_LINK"] = meet_link
        os.environ["DURATION_IN_MINUTES"] = str(duration)
        asyncio.run(join_meet())

if __name__ == "__main__":
    main()