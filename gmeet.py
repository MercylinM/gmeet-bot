import asyncio
import os
import subprocess
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
    'audio_streamer': None 
}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'gmeet-bot',
        'bot_status': bot_state['status'],
        'current_meeting': bot_state['current_meeting'],
        'uptime': (datetime.datetime.now() - bot_state['start_time']).total_seconds() if bot_state['start_time'] else 0
    })

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
        self.listen_task = None
        self.bytes_transmitted = 0
        self.last_activity_time = datetime.datetime.now()
        self.is_connected = False

        
    async def connect_websocket(self):
        """Connect to backend WebSocket for real-time audio streaming"""
        try:
            headers = {}
            if not self.ws_url.startswith('ws://localhost') and not self.ws_url.startswith('ws://127.0.0.1'):
                headers['Origin'] = 'https://gmeet-bot.onrender.com'
            
            self.websocket = await websockets.connect(
                self.ws_url,
                extra_headers=headers,
                ping_interval=20,
                ping_timeout=10
            )
            print(f"Connected to WebSocket: {self.ws_url}")
            self.is_connected = True
            
            self.listen_task = asyncio.create_task(self._listen_for_transcripts())
            return True
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            self.is_connected = False
            return False
        
    async def _listen_for_transcripts(self):
        """Listen for transcript messages on the same WebSocket"""
        try:
            while self.websocket and self._is_websocket_open():
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=1.0
                    )
                    self.handle_transcript_message(message)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    print("WebSocket connection closed")
                    self.is_connected = False
                    break
        except Exception as e:
            print(f"Error listening for transcripts: {e}")
            self.is_connected = False
    
    def _is_websocket_open(self):
        """Check if the WebSocket connection is open"""
        if not self.websocket:
            return False
        if hasattr(self.websocket, 'closed'):
            return not self.websocket.closed
        elif hasattr(self.websocket, 'state'):
            return self.websocket.state == 1  
        return False
    
    def handle_transcript_message(self, message):
        """Handle incoming transcript messages"""
        try:
            data = json.loads(message)
            message_type = data.get('message_type', '')
            
            if message_type == 'interim_transcript':
                transcript = data.get('transcript', '').strip()
                speaker = data.get('speaker_name', 'Unknown')
                if transcript:
                    print(f"INTERIM [{speaker}]: {transcript}")
                    
            elif message_type == 'final_transcript':
                transcript = data.get('transcript', '').strip()
                speaker = data.get('speaker_name', 'Unknown')
                if transcript:
                    print(f"FINAL [{speaker}]: {transcript}")
                    
            elif message_type == 'enriched_transcript':
                transcript = data.get('transcript', '').strip()
                speaker = data.get('speaker_name', 'Unknown')
                analysis = data.get('analysis', {})
                
                if transcript and analysis:
                    summary = analysis.get('summary', '').strip()
                    questions = analysis.get('questions', [])
                    keywords = analysis.get('keywords', [])
                    
                    print(f"\nENRICHED [{speaker}]: {transcript}")
                    if summary:
                        print(f"Summary: {summary}")
                    if keywords:
                        print(f"Keywords: {', '.join(keywords)}")
                    if questions:
                        for i, q in enumerate(questions, 1):
                            print(f"Question {i}: {q}")
                    print()
                        
            elif message_type == 'analysis_error':
                print(f"Analysis error: {data.get('error', 'Unknown error')}")
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Error handling transcript: {e}")
    
    def start_realtime_streaming(self, duration_minutes=15):
        """Start real-time audio streaming to backend"""
        self.is_streaming = True
        duration_seconds = duration_minutes * 60
        
        streaming_thread = threading.Thread(
            target=self._stream_audio_realtime,
            args=(duration_seconds,)
        )
        streaming_thread.daemon = True
        streaming_thread.start()
        
        return streaming_thread
        
    def _stream_audio_realtime(self, duration_seconds):
        """Stream audio to backend WebSocket in real-time"""
        asyncio.new_event_loop().run_until_complete(
            self._stream_audio_async(duration_seconds)
        )
    
    async def _stream_audio_async(self, duration_seconds):
        """Async method to handle WebSocket streaming"""
        if not await self.connect_websocket():
            return
            
        try:
            try:
                subprocess.run(["sox", "--version"], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("Error: sox is not installed or not in PATH")
                return
            
            audio_format = {
                'format': 's16le',
                'rate': 16000,
                'channels': 1,
                'bits': 16,
                'encoding': 'signed-integer'
            }
            
            audio_device = os.getenv("AUDIO_DEVICE", "default")
            
            if audio_device.startswith("pulseaudio:"):
                device_name = audio_device.replace("pulseaudio:", "")
                parec_command = [
                    "parec",
                    f"--format={audio_format['format']}",
                    f"--rate={audio_format['rate']}",
                    f"--channels={audio_format['channels']}",
                    "--monitor-stream=false",
                    device_name
                ]
                
                sox_command = [
                    "sox",
                    "-q",
                    "-t", "raw",
                    "-r", str(audio_format['rate']),
                    "-c", str(audio_format['channels']),
                    "-b", str(audio_format['bits']),
                    "-e", audio_format['encoding'],
                    "-",
                    "-t", "raw", "-"
                ]
                
                print(f"Starting PulseAudio capture for device: {device_name}")
                print(f"Parec command: {' '.join(parec_command)}")
                print(f"Sox command: {' '.join(sox_command)}")
                
                parec_process = subprocess.Popen(
                    parec_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                sox_process = subprocess.Popen(
                    sox_command,
                    stdin=parec_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                self.stream_process = sox_process
                
            elif audio_device and "monitor" in audio_device:
                parec_command = [
                    "parec",
                    f"--format={audio_format['format']}",
                    f"--rate={audio_format['rate']}",
                    f"--channels={audio_format['channels']}",
                    "--monitor-stream=true",
                    audio_device
                ]
                
                sox_command = [
                    "sox",
                    "-q",
                    "-t", "raw",
                    "-r", str(audio_format['rate']),
                    "-c", str(audio_format['channels']),
                    "-b", str(audio_format['bits']),
                    "-e", audio_format['encoding'],
                    "-",
                    "-t", "raw", "-"
                ]
                
                print(f"Starting monitor capture for device: {audio_device}")
                print(f"Parec command: {' '.join(parec_command)}")
                print(f"Sox command: {' '.join(sox_command)}")
                
                parec_process = subprocess.Popen(
                    parec_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                sox_process = subprocess.Popen(
                    sox_command,
                    stdin=parec_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                self.stream_process = sox_process
                
            else:
                sox_command = [
                    "sox",
                    "-q",
                    "-d" if audio_device == "default" else audio_device,
                    "-r", str(audio_format['rate']),
                    "-c", str(audio_format['channels']),
                    "-b", str(audio_format['bits']),
                    "-e", audio_format['encoding'],
                    "-t", "raw", "-"
                ]
                
                print(f"Starting default capture for device: {audio_device}")
                print(f"Sox command: {' '.join(sox_command)}")
                
                self.stream_process = subprocess.Popen(
                    sox_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
            print(f"Starting real-time audio streaming for {duration_seconds} seconds...")
            
            start_time = datetime.datetime.now()
            chunk_size = 4096
            silence_timer = None

            ping_task = asyncio.create_task(self._ping_websocket())
            
            while (self.is_streaming and 
                   self.stream_process and 
                   self.stream_process.poll() is None) and bot_state['status'] != 'stopping':
                
                audio_data = self.stream_process.stdout.read(chunk_size)
                if not audio_data:
                    await asyncio.sleep(0.01)
                    continue
                
                if self.websocket and self._is_websocket_open():
                    try:
                        await self.websocket.send(audio_data)
                        self.bytes_transmitted += len(audio_data)
                        self.last_activity_time = datetime.datetime.now()
                        
                        if silence_timer:
                            silence_timer.cancel()
                        
                        silence_timer = asyncio.create_task(
                            self._check_silence(300)
                        )
                        
                        elapsed = (datetime.datetime.now() - start_time).total_seconds()
                        if int(elapsed) % 5 == 0 and elapsed > 0:
                            kb_transmitted = self.bytes_transmitted / 1024
                            print(f"📊 Streaming: {kb_transmitted:.2f} KB sent in {int(elapsed)}s")
                            
                    except Exception as e:
                        print(f"WebSocket send error: {e}")
                        self.is_connected = False
                        break
                
                elapsed = (datetime.datetime.now() - start_time).total_seconds()
                if elapsed >= duration_seconds:
                    print(f"Reached duration limit: {duration_seconds}s")
                    break
                
            ping_task.cancel()
                             
        except Exception as e:
            print(f"Real-time streaming error: {e}")
        finally:
            await self.cleanup()

    async def _ping_websocket(self):
        """Send periodic pings to keep the WebSocket connection alive"""
        try:
            while self.is_connected and self.websocket:
                await asyncio.sleep(30)  # Ping every 30 seconds
                if self.websocket and self._is_websocket_open():
                    try:
                        await self.websocket.ping()
                        print("WebSocket ping sent")
                    except Exception as e:
                        print(f"WebSocket ping failed: {e}")
                        self.is_connected = False
                        break
        except asyncio.CancelledError:
            pass
    
    async def _check_silence(self, max_silence_seconds):
        """Check for silence and emit an event if detected"""
        try:
            await asyncio.sleep(max_silence_seconds)
            time_since_last_activity = (datetime.datetime.now() - self.last_activity_time).total_seconds()
            if time_since_last_activity >= max_silence_seconds:
                print("No audio data for extended period, checking connection...")
        except asyncio.CancelledError:
            pass
    
    async def cleanup(self):
        """Clean up WebSocket connection and processes"""
        print("Cleaning up audio streamer...")
        self.is_streaming = False
        self.is_connected = False
        
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
            self.listen_task = None
        
        if self.stream_process:
            self.stream_process.terminate()
            try:
                self.stream_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.stream_process.kill()
            self.stream_process = None
            
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            
        print("Real-time streaming stopped")
        print(f"Total bytes transmitted: {self.bytes_transmitted / 1024:.2f} KB")

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
        

    print("Cleaning screenshots")
    if os.path.exists("screenshots"):
        for f in os.listdir("screenshots"):
            os.remove(f"screenshots/{f}")
    else:
        os.mkdir("screenshots")

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
        options.add_argument("--window-size=1920x1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-port=9222")
        
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
    driver.set_window_size(1920, 1080)

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
    sleep(5)

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

    print("Taking screenshot")
    driver.save_screenshot("screenshots/initial.png")

    try:
        driver.find_element(
            By.XPATH,
            "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
        ).click()
        sleep(2)
    except:
        print("No popup")

    print("Disable microphone")
    sleep(10)

    missing_mic = False

    try:
        print("Try to dismiss missing mic")
        driver.find_element(By.CLASS_NAME, "VfPpkd-vQzf8d").find_element(By.XPATH, "..")
        sleep(2)
        driver.save_screenshot("screenshots/missing_mic.png")

        with open("screenshots/webpage.html", "w") as f:
            f.write(driver.page_source)
        missing_mic = True
    except:
        pass

    try:
        print("Allow Microphone")
        driver.find_element(
            By.XPATH,
            "/html/body/div/div[3]/div[2]/div/div/div/div/div[2]/div/div[1]/button",
        ).click()
        sleep(2)
        driver.save_screenshot("screenshots/allow_microphone.png")
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

    sleep(2)
    driver.save_screenshot("screenshots/disable_microphone.png")

    print("Disable camera")
    if not missing_mic:
        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[1]/div[1]/div/div[6]/div[2]/div',
        ).click()
        sleep(2)
    else:
        print("assuming missing mic = missing camera")
    driver.save_screenshot("screenshots/disable_camera.png")
    
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
                sleep(2)
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
                    sleep(5)
                    driver.save_screenshot("screenshots/join_button_clicked.png")
                    button_clicked = True
                    break
                except:
                    continue
            
            if not button_clicked:
                print("Could not find or click the join button")
    except Exception as e:
        print(f"Error setting name: {e}")
        driver.save_screenshot("screenshots/name_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    try:
        print("Looking for any join button...")
        wait = WebDriverWait(driver, 10)
        
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
                driver.save_screenshot("screenshots/join_button_clicked.png")
                joined = True
                break
            except TimeoutException:
                continue
        
        if not joined:
            print("Could not find any join button")
            driver.save_screenshot("screenshots/no_join_button.png")
    except Exception as e:
        print(f"Error handling join button: {e}")
        driver.save_screenshot("screenshots/join_button_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return

    print("Waiting for meeting to load...")
    sleep(10)
    driver.save_screenshot("screenshots/meeting_loading.png")

    try:
        wait = WebDriverWait(driver, 10)
        
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
            driver.save_screenshot("screenshots/in_meeting.png")
        else:
            print("Could not confirm if in meeting, proceeding anyway...")
            driver.save_screenshot("screenshots/meeting_status_unknown.png")
    except Exception as e:
        print(f"Error checking meeting status: {e}")
        driver.save_screenshot("screenshots/meeting_status_error.png")

    if bot_state['status'] == 'stopping':
        print("Stop signal received, cleaning up")
        cleanup_bot()
        return
    

    duration_minutes = int(os.getenv("DURATION_IN_MINUTES", "15"))
    duration_seconds = duration_minutes * 60

    audio_streamer = RealtimeAudioStreamer(backend_url)
    bot_state['audio_streamer'] = audio_streamer

    print("\nStarting recording and streaming...")
    print(f"Duration: {duration_minutes} minutes")
    
    streaming_thread = audio_streamer.start_realtime_streaming(duration_minutes)
    print(f"Recording for {duration_minutes} minutes...")

    elapsed = 0
    while elapsed < duration_seconds and bot_state['status'] != 'stopping':
        await asyncio.sleep(1)
        elapsed += 1
        
        if elapsed == 30 and audio_streamer.bytes_transmitted == 0:
            print("WARNING: No audio data transmitted after 30 seconds!")

    
    # await asyncio.sleep(duration_seconds)

    print("\nRecording completed!")
    
    streaming_thread.join(timeout=10)

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
    """Run production server with better configuration"""
    port = int(os.getenv('PORT', 10000))
    workers = int(os.getenv('WORKERS', 2))
    
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
            'workers': workers,
            'timeout': 120,
            'accesslog': '-',
            'errorlog': '-'
        }
        
        print(f"Starting production server on port {port} with {workers} workers")
        GunicornApp(app, options).run()
        
    except ImportError:
        print("Gunicorn not available, falling back to Flask development server")
        app.run(host='0.0.0.0', port=port, debug=False)

@click.command()
@click.option('--meet-link', help='Google Meet link')
@click.option('--duration', default=15, help='Duration in minutes')
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
