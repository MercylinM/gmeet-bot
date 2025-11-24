import asyncio
import os
import subprocess
import time
import click
import datetime
import requests
import json
import websockets
import threading
from time import sleep
import re
import sys
from flask import Flask, request, jsonify
from flask_cors import CORS
from queue import Queue, Empty


import undetected_chromedriver as uc
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

app = Flask(__name__)
CORS(app)

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
            response = requests.get(health_url)
            if response.status_code == 200:
                print("Keep-alive ping successful")
            else:
                print(f"Keep-alive ping failed with status: {response.status_code}")

            
            time.sleep(600)
        except Exception as e:
            print(f"Keep-alive error: {e}")
            time.sleep(60)  

keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
keep_alive_thread.start()


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'gmeet-bot',
        'bot_status': bot_state['status'],
        'current_meeting': bot_state['current_meeting'],
        'uptime': (datetime.datetime.now() - bot_state['start_time']).total_seconds() if bot_state['start_time'] else 0
    }), 200

@app.route('/start', methods=['POST'])
def start_bot():
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

        os.environ['GMEET_LINK'] = meet_link
        os.environ['DURATION_IN_MINUTES'] = str(duration)

        bot_state['status'] = 'starting'
        bot_state['current_meeting'] = meet_link
        bot_state['start_time'] = datetime.datetime.now()

        def run_bot():
            try:
                asyncio.run(join_meet())
            except Exception as e:
                print(f"Error in bot thread: {e}")
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
            'message': 'Bot is starting and will join the meeting shortly'
        })

    except Exception as e:
        print(f"Error starting bot: {e}")
        bot_state['status'] = 'error'
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    
@app.route('/stop', methods=['POST'])
def stop_bot():
    try:
        if bot_state['status'] == 'idle':
            return jsonify({
                'success': True,
                'message': 'Bot is not running'
            })

        print("Stop signal received, cleaning up bot...")
        bot_state['status'] = 'stopping'
        
        cleanup_bot()
        
        return jsonify({
            'success': True,
            'message': 'Bot stopped successfully'
        })

    except Exception as e:
        print(f"Error stopping bot: {e}")
        bot_state['status'] = 'error'
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    
def cleanup_bot():
    """Cleanup bot resources - stop audio, quit driver"""
    print("Cleaning up bot resources...")
    
    if bot_state['audio_streamer']:
        try:
            bot_state['audio_streamer'].is_streaming = False
            if bot_state['audio_streamer'].stream_process:
                bot_state['audio_streamer'].stream_process.terminate()
            print("Audio streamer stopped")
        except Exception as e:
            print(f"Error stopping audio streamer: {e}")
    
    if bot_state['driver']:
        try:
            bot_state['driver'].quit()
            print("Chrome driver quit")
        except Exception as e:
            print(f"Error quitting driver: {e}")
    
    bot_state['status'] = 'idle'
    bot_state['current_meeting'] = None
    bot_state['driver'] = None
    bot_state['audio_streamer'] = None
    
    print("Bot cleanup complete")

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'success': True,
        'status': bot_state['status'],
        'isRunning': bot_state['status'] == 'running',
        'current_meeting': bot_state['current_meeting'],
        'uptime': (datetime.datetime.now() - bot_state['start_time']).total_seconds() if bot_state['start_time'] else 0
    })

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'service': 'Google Meet Bot',
        'version': '1.0.0',
        'endpoints': {
            'health': '/health',
            'start': 'POST /start',
            'stop': 'POST /stop',
            'status': '/status'
        }
    })

class RealtimeAudioStreamer:
    def __init__(self, backend_url):
        self.backend_url = backend_url
        self.ws_url = backend_url.replace('http', 'ws') + '/ws/audio'
        self.websocket = None
        self.is_streaming = False
        self.stream_process = None
        self.bytes_transmitted = 0
        self.last_activity_time = datetime.datetime.now()
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5
        self.audio_queue = Queue()
        self._stop_event = threading.Event()
        
    async def connect_websocket(self):
        """Connect to backend WebSocket for audio streaming"""
        try:
            print(f"Connecting to audio WebSocket: {self.ws_url}")
            
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=None,  
            )
            
            print("Connected to audio WebSocket")
            self.is_connected = True
            self.reconnect_attempts = 0
            return True
            
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            self.is_connected = False
            self.reconnect_attempts += 1
            return False

    def _is_websocket_open(self):
        """Check if WebSocket connection is open - FIXED VERSION"""
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

    def start_realtime_streaming(self, duration_minutes=60):
        """Start real-time audio streaming to backend"""
        if self.is_streaming:
            print("Audio streaming already running")
            return None

        self.is_streaming = True
        self._stop_event.clear()
        
        capture_thread = threading.Thread(
            target=self._capture_audio,
            daemon=True,
            name="AudioCaptureThread"
        )
        capture_thread.start()
        
        sender_thread = threading.Thread(
            target=self._run_websocket_sender,
            daemon=True,
            name="WebSocketSenderThread"
        )
        sender_thread.start()
        
        return [capture_thread, sender_thread]

    def _capture_audio(self):
        """Capture audio using sox and put it in the queue"""
        print("Starting audio capture...")

        try:
            subprocess.run(["sox", "--version"], capture_output=True, check=True)
            print("Sox is available")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Error: sox is not installed or not in PATH")
            self.is_streaming = False
            return

        # Check if we're in production (e.g., by environment variable)
        in_production = os.getenv("FLASK_ENV", "").lower() == "production" or \
                        os.getenv("RUN_AS_SERVER", "").lower() == "true"

        if in_production:
            # Use PulseAudio virtual speaker monitor for capturing output audio
            try:
                result = subprocess.run(
                    ["pactl", "list", "short", "sources"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                print(f"Available audio sources:\n{result.stdout}")
            except Exception as e:
                print(f"Could not list audio sources: {e}")

            audio_source = "virtual_speaker.monitor"  # Your virtual sink monitor

            parec_command = [
                "parec",
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                f"--device={audio_source}"
            ]

            sox_command = [
                "sox",
                "-q",
                "-t", "raw",
                "-r", "16000",
                "-c", "1",
                "-b", "16",
                "-e", "signed-integer",
                "-",  # stdin from parec
                "-t", "raw",
                "-"
            ]

            print(f"Parec command: {' '.join(parec_command)}")
            print(f"Sox command: {' '.join(sox_command)}")

            try:
                parec_process = subprocess.Popen(
                    parec_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=4096
                )
                sox_process = subprocess.Popen(
                    sox_command, stdin=parec_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=4096
                )
                parec_process.stdout.close()
                self.stream_process = sox_process
                print(f"Started audio capture (Sox PID: {sox_process.pid}, Parec PID: {parec_process.pid})")

                chunk_size = 2048

                while self.is_streaming and not self._stop_event.is_set() and self.stream_process and self.stream_process.poll() is None:
                    try:
                        audio_data = self.stream_process.stdout.read(chunk_size)
                        if not audio_data:
                            time.sleep(0.1)
                            continue
                        self.audio_queue.put(audio_data)
                        self.bytes_transmitted += len(audio_data)
                        self.last_activity_time = datetime.datetime.now()

                        if self.bytes_transmitted % (100 * 1024) < chunk_size:
                            print(f"📊 Audio captured: {self.bytes_transmitted / 1024:.2f} KB")
                    except Exception as e:
                        print(f"Error reading audio data: {e}")
                        break

            except Exception as e:
                print(f"Audio capture error: {e}")
            finally:
                self._cleanup_audio_capture()

        else:
            # Original localhost capture method for simplicity, like "sox -d" capturing mic directly
            sox_command = [
                "sox",
                "-q",
                "-d",  # default audio device (microphone)
                "-r", "16000",
                "-c", "1",
                "-b", "16",
                "-e", "signed-integer",
                "-t", "raw",
                "-"
            ]
            print(f"Sox command (localhost): {' '.join(sox_command)}")
            try:
                self.stream_process = subprocess.Popen(
                    sox_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=4096
                )
                chunk_size = 2048

                while self.is_streaming and not self._stop_event.is_set() and self.stream_process and self.stream_process.poll() is None:
                    try:
                        audio_data = self.stream_process.stdout.read(chunk_size)
                        if not audio_data:
                            time.sleep(0.1)
                            continue
                        self.audio_queue.put(audio_data)
                        self.bytes_transmitted += len(audio_data)
                        self.last_activity_time = datetime.datetime.now()

                        if self.bytes_transmitted % (100 * 1024) < chunk_size:
                            print(f"📊 Audio captured: {self.bytes_transmitted / 1024:.2f} KB")
                    except Exception as e:
                        print(f"Error reading audio data: {e}")
                        break

            except Exception as e:
                print(f"Audio capture error (localhost): {e}")
            finally:
                self._cleanup_audio_capture()

    def _run_websocket_sender(self):
        """Run WebSocket sender in a separate event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._websocket_sender_async())
        finally:
            loop.close()

    async def _websocket_sender_async(self):
        """Async WebSocket sender that reads from queue and sends to server"""
        print("Starting WebSocket sender...")
        
        if not await self.connect_websocket():
            print("Failed initial WebSocket connection")
            self.is_streaming = False
            return

        last_stats_time = datetime.datetime.now()
        
        while self.is_streaming and not self._stop_event.is_set():
            try:
                try:
                    audio_data = self.audio_queue.get(timeout=1.0)
                except Empty:
                    continue

                if self._is_websocket_open():
                    try:
                        await self.websocket.send(audio_data)
                        
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
                        
                        if not await self._reconnect_websocket():
                            print("Failed to reconnect WebSocket")
                            break
                
                self.audio_queue.task_done()
                
            except Exception as e:
                print(f"WebSocket sender error: {e}")
                await asyncio.sleep(0.1)

        print("WebSocket sender stopped")

    async def _reconnect_websocket(self):
        """Attempt to reconnect WebSocket with backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            print("Max reconnection attempts reached")
            return False

        delay = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 60)
        print(f"Attempting reconnect in {delay}s (attempt {self.reconnect_attempts + 1})")
        
        await asyncio.sleep(delay)
        
        if await self.connect_websocket():
            print("WebSocket reconnected successfully")
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
        if self.stream_process:
            print(f"Stopping audio process (PID: {self.stream_process.pid})...")
            try:
                self.stream_process.terminate()
                try:
                    self.stream_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.stream_process.kill()
                    self.stream_process.wait()
            except Exception as e:
                print(f"Error stopping audio process: {e}")
            finally:
                self.stream_process = None

    async def cleanup(self):
        """Clean up all streaming resources"""
        print("Cleaning up audio streamer...")
        self.is_streaming = False
        self._stop_event.set()
        self.is_connected = False
        
        self._cleanup_audio_capture()
        
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Empty:
                break
        
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.close()
                print("Audio WebSocket connection closed")
            except Exception as e:
                print(f"Error closing WebSocket: {e}")
            self.websocket = None
        
        print(f"Final stats: {self.bytes_transmitted / 1024:.2f} KB transmitted total")

    def stop_streaming(self):
        """Stop streaming synchronously"""
        self.is_streaming = False
        self._stop_event.set()
        
    def get_status(self):
        """Get current streaming status"""
        return {
            'is_streaming': self.is_streaming,
            'is_connected': self.is_connected,
            'bytes_transmitted': self.bytes_transmitted,
            'queue_size': self.audio_queue.qsize(),
            'reconnect_attempts': self.reconnect_attempts
        }
    
def make_request(url, headers, method="GET", data=None, files=None):
    if method == "POST":
        response = requests.post(url, headers=headers, json=data, files=files)
    else:
        response = requests.get(url, headers=headers)
    return response.json()


async def google_sign_in(email, password, driver):
    driver.get("https://accounts.google.com")
    sleep(1)
    
    email_field = driver.find_element(By.NAME, "identifier")
    email_field.send_keys(email)
    driver.save_screenshot("screenshots/email.png")
    sleep(2)
    
    driver.find_element(By.ID, "identifierNext").click()
    sleep(3)
    driver.save_screenshot("screenshots/password.png")
    
    password_field = driver.find_element(By.NAME, "Passwd")
    password_field.click()
    password_field.send_keys(password)
    password_field.send_keys(Keys.RETURN)
    sleep(5)
    driver.save_screenshot("screenshots/signed_in.png")


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
    bot_state['status'] = 'running'

    cleanup_chrome_processes()

    meet_link = os.getenv("GMEET_LINK", "https://meet.google.com/mhj-bcdx-bgu")
    backend_url = os.getenv("BACKEND_URL", "http://localhost:3000")
    
    print(f"Starting recorder for {meet_link}")
    print(f"Using backend: {backend_url}")

    if bot_state['status'] == 'stopping':
        print("Stop signal received before starting, aborting")
        cleanup_bot()
        return

    try:
        health_response = requests.get(f"{backend_url}/health", timeout=5)
        if health_response.ok:
            print(f"Backend is healthy: {health_response.json()}")
        else:
            print(f"Backend health check failed: {health_response.status_code}")
            
    except Exception as e:
        print(f"Cannot connect to backend: {e}")
        

    # print("Cleaning screenshots")
    # if os.path.exists("screenshots"):
    #     for f in os.listdir("screenshots"):
    #         os.remove(f"screenshots/{f}")
    # else:
    #     os.mkdir("screenshots")

    print("Setting up audio recording with sox")
    try:
        subprocess.run(["sox", "--version"], capture_output=True, check=True)
        print("sox is available for audio recording")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: sox is not installed or not in PATH")

    driver = None

    try:
        chrome_version = get_chrome_version()
        print(f"Detected Chrome version: {chrome_version}")
        
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
    except Exception as e:
        print(f"Error initializing Chrome driver: {e}")
        
        try:
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
        except Exception as e2:
            print(f"Error with fallback Chrome driver: {e2}")
            bot_state['status'] = 'error'
            cleanup_bot()
            return
        
        if not driver:
            print("Failed to initialize Chrome driver")
            bot_state['status'] = 'error'
            cleanup_bot()
            return
    
    bot_state['driver'] = driver
    driver.set_window_size(1280, 720)

    email = os.getenv("GMAIL_USER_EMAIL", "")
    password = os.getenv("GMAIL_USER_PASSWORD", "")

    if email == "" or password == "":
        print("Error: No email or password specified")
        driver.quit()
        bot_state['status'] = 'error'
        cleanup_bot()
        return

    print("Google Sign in")
    await google_sign_in(email, password, driver)

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    print(f"Navigating to meet link: {meet_link}")
    driver.get(meet_link)
    sleep(3)

    try:
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
    except Exception as e:
        print(f"Warning: Could not grant permissions: {e}")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    # print("Taking screenshot")
    # driver.save_screenshot("screenshots/initial.png")

    try:
        driver.find_element(
            By.XPATH,
            "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
        ).click()
        sleep(1)
    except:
        print("No popup")

    print("Disable microphone")
    sleep(5)

    missing_mic = False

    try:
        print("Try to dismiss missing mic")
        driver.find_element(By.CLASS_NAME, "VfPpkd-vQzf8d").find_element(By.XPATH, "..")
        sleep(1)
        # driver.save_screenshot("screenshots/missing_mic.png")

        # with open("screenshots/webpage.html", "w") as f:
        #     f.write(driver.page_source)
        missing_mic = True
    except:
        pass

    try:
        print("Allow Microphone")
        driver.find_element(
            By.XPATH,
            "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
        ).click()
        sleep(1)
        # driver.save_screenshot("screenshots/allow_microphone.png")
    except:
        print("No Allow Microphone popup")

    try:
        print("Try to disable microphone")
        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[1]/div[1]/div/div[6]/div[1]/div/div',
        ).click()
    except:
        print("No microphone to disable")

    sleep(1)
    # driver.save_screenshot("screenshots/disable_microphone.png")

    print("Disable camera")
    if not missing_mic:
        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[1]/div[1]/div/div[6]/div[2]/div',
        ).click()
        sleep(1)
    else:
        print("assuming missing mic = missing camera")
    # driver.save_screenshot("screenshots/disable_camera.png")
    
    try:
        print("Try to set name")
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
                driver.save_screenshot("screenshots/give_non_registered_name.png")
                name_set = True
                break
            except:
                continue
        
        if name_set:
            print("Name set successfully")
            join_button_selectors = [
                '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[2]/div[1]/div[1]/button/span',
                '//button[contains(text(), "Join now")]',
                '//button[contains(text(), "Ask to join")]',
                '//button[contains(text(), "Continue")]',
                '//button[contains(text(), "Join")]'
            ]
            
            button_clicked = False
            for selector in join_button_selectors:
                try:
                    join_button = driver.find_element(By.XPATH, selector)
                    join_button.click()
                    sleep(2)
                    # driver.save_screenshot("screenshots/join_button_clicked.png")
                    button_clicked = True
                    break
                except:
                    continue
            
            if not button_clicked:
                print("Could not find or click the join button")
    except Exception as e:
        print(f"Error setting name: {e}")
        # driver.save_screenshot("screenshots/name_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    try:
        print("Looking for any join button...")
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
                print(f"Clicked join button using selector: {selector}")
                # driver.save_screenshot("screenshots/join_button_clicked.png")
                joined = True
                break
            except TimeoutException:
                continue
        
        if not joined:
            print("Could not find any join button")
            # driver.save_screenshot("screenshots/no_join_button.png")
    except Exception as e:
        print(f"Error handling join button: {e}")
        # driver.save_screenshot("screenshots/join_button_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    print("Waiting for meeting to load...")
    sleep(10)
    # driver.save_screenshot("screenshots/meeting_loading.png")

    try:
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
                print(f"Detected meeting using selector: {selector}")
                break
            except TimeoutException:
                continue
        
        if in_meeting:
            print("Successfully joined the meeting!")
            # driver.save_screenshot("screenshots/in_meeting.png")
        else:
            print("Could not confirm if in meeting, proceeding anyway...")
            # driver.save_screenshot("screenshots/meeting_status_unknown.png")
    except Exception as e:
        print(f"Error checking meeting status: {e}")
        # driver.save_screenshot("screenshots/meeting_status_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return
    
    # try:
    #     print("Attempting to go fullscreen...")
    #     driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.F11)
    #     sleep(1)
    #     print("Pressed F11 to go fullscreen")
    # except Exception as e:
    #     print(f"Error going fullscreen: {e}")

    duration_minutes = int(os.getenv("DURATION_IN_MINUTES", "60"))  
    duration_seconds = duration_minutes * 60

    audio_streamer = RealtimeAudioStreamer(backend_url)
    bot_state['audio_streamer'] = audio_streamer

    print("\nStarting recording and streaming...")
    print(f"Duration: {duration_minutes} minutes")
    
    streaming_thread = audio_streamer.start_realtime_streaming(duration_minutes)
    print(f"Recording for {duration_minutes} minutes...")

    elapsed = 0
    last_status_check = 0
    status_check_interval = 60  
    
    while elapsed < duration_seconds and bot_state['status'] != 'stopping':
        await asyncio.sleep(1)
        elapsed += 1
        
        if elapsed - last_status_check >= status_check_interval:
            if elapsed == 30 and audio_streamer.bytes_transmitted == 0:
                print("WARNING: No audio data transmitted after 30 seconds!")
            
            if not audio_streamer.is_connected:
                print(f"WARNING: WebSocket disconnected at {elapsed} seconds")
                
            print(f"Status check at {elapsed}s: Connected={audio_streamer.is_connected}, "
                  f"Bytes sent={audio_streamer.bytes_transmitted/1024:.2f}KB")
            last_status_check = elapsed
    
    
    if streaming_thread:
        for thread in streaming_thread:
            if thread.is_alive():
                thread.join(timeout=10)

    print("Cleaning up session...")
    if driver:
        try:
            driver.quit()
            print("Chrome driver quit")
        except Exception as e:
            print(f"Error quitting driver: {e}")
    
    bot_state['status'] = 'idle'
    bot_state['driver'] = None
    bot_state['audio_streamer'] = None
    bot_state['current_meeting'] = None
    
    print("Bot session ended cleanly")

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

