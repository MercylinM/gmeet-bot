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

import undetected_chromedriver as uc
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By


class RealtimeAudioStreamer:
    def __init__(self, backend_url):
        self.backend_url = backend_url
        self.ws_url = backend_url.replace('http', 'ws') + '/ws/audio'
        self.websocket = None
        self.is_streaming = False
        self.stream_process = None
        
    async def connect_websocket(self):
        """Connect to backend WebSocket for real-time audio streaming"""
        try:
            self.websocket = await websockets.connect(self.ws_url)
            print(f"Connected to WebSocket: {self.ws_url}")
            return True
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            return False
    
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
            ffmpeg_command = [
                "ffmpeg",
                "-f", "pulse",
                "-i", "default",
                "-t", str(duration_seconds),
                "-ac", "1",
                "-ar", "16000",
                "-acodec", "pcm_s16le",
                "-f", "wav",
                "pipe:1"
            ]
            
            print(f"🎙️ Starting real-time audio streaming...")
            self.stream_process = subprocess.Popen(
                ffmpeg_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            
            start_time = datetime.datetime.now()
            chunk_size = 1024 * 8  # 8KB chunks
            
            while (self.is_streaming and 
                   self.stream_process and 
                   self.stream_process.poll() is None):
                
                audio_data = self.stream_process.stdout.read(chunk_size)
                if not audio_data:
                    await asyncio.sleep(0.1)
                    continue
                
                if self.websocket:
                    try:
                        await self.websocket.send(audio_data)
                    except Exception as e:
                        print(f"WebSocket send error: {e}")
                        break
                
                # Check duration
                elapsed = (datetime.datetime.now() - start_time).total_seconds()
                if elapsed >= duration_seconds:
                    break
                    
        except Exception as e:
            print(f"Real-time streaming error: {e}")
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """Clean up WebSocket connection"""
        self.is_streaming = False
        
        if self.stream_process:
            self.stream_process.terminate()
            self.stream_process.wait()
            self.stream_process = None
            
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            
        print("Real-time streaming stopped")


class TranscriptListener:
    def __init__(self, backend_url):
        self.backend_url = backend_url
        self.ws_url = backend_url.replace('http', 'ws') + '/ws/transcripts'
        self.is_listening = False
        
    async def start_listening(self):
        """Listen to transcript WebSocket for real-time results"""
        self.is_listening = True
        
        try:
            async with websockets.connect(self.ws_url) as websocket:
                print(f"👂 Listening to transcripts: {self.ws_url}")
                
                while self.is_listening:
                    try:
                        message = await asyncio.wait_for(
                            websocket.recv(), 
                            timeout=1.0
                        )
                        
                        if message:
                            self.handle_transcript_message(message)
                            
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"Transcript listening error: {e}")
                        break
                        
        except Exception as e:
            print(f"Failed to connect to transcript WebSocket: {e}")
    
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
                    
                    print(f"🤖 ENRICHED [{speaker}]: {transcript}")
                    if summary:
                        print(f"Summary: {summary}")
                    if questions:
                        print(f"Questions: {', '.join(questions)}")
                        
            elif message_type == 'analysis_error':
                print(f"Analysis error: {data.get('error', 'Unknown error')}")
                
        except json.JSONDecodeError:
            print(f"Raw message: {message}")
        except Exception as e:
            print(f"Error handling transcript: {e}")


def make_request(url, headers, method="GET", data=None, files=None):
    if method == "POST":
        response = requests.post(url, headers=headers, json=data, files=files)
    else:
        response = requests.get(url, headers=headers)
    return response.json()


async def run_command_async(command):
    process = await asyncio.create_subprocess_shell(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return stdout, stderr


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


async def join_meet():
    meet_link = os.getenv("GMEET_LINK", "https://meet.google.com/mhj-bcdx-bgu")
    backend_url = os.getenv("BACKEND_URL", "http://localhost:10000")
    
    print(f"🎯 Starting recorder for {meet_link}")
    print(f"🔗 Using backend: {backend_url}")

    print("Cleaning screenshots")
    if os.path.exists("screenshots"):
        for f in os.listdir("screenshots"):
            os.remove(f"screenshots/{f}")
    else:
        os.mkdir("screenshots")

    print("Starting virtual audio drivers")
    subprocess.check_output(
        "sudo rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse", shell=True
    )
    subprocess.check_output(
        "sudo pulseaudio -D --verbose --exit-idle-time=-1 --system --disallow-exit  >> /dev/null 2>&1",
        shell=True,
    )
    subprocess.check_output(
        'sudo pactl load-module module-null-sink sink_name=DummyOutput sink_properties=device.description="Virtual_Dummy_Output"',
        shell=True,
    )
    subprocess.check_output(
        'sudo pactl load-module module-null-sink sink_name=MicOutput sink_properties=device.description="Virtual_Microphone_Output"',
        shell=True,
    )
    subprocess.check_output(
        "sudo pactl set-default-source MicOutput.monitor", shell=True
    )
    subprocess.check_output("sudo pactl set-default-sink MicOutput", shell=True)
    subprocess.check_output(
        "sudo pactl load-module module-virtual-source source_name=VirtualMic",
        shell=True,
    )

    # Setup browser
    options = uc.ChromeOptions()
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--window-size=1920x1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    log_path = "chromedriver.log"

    driver = uc.Chrome(service_log_path=log_path, use_subprocess=False, options=options)
    driver.set_window_size(1920, 1080)

    email = os.getenv("GMAIL_USER_EMAIL", "")
    password = os.getenv("GMAIL_USER_PASSWORD", "")

    if email == "" or password == "":
        print("No email or password specified")
        return

    print("Google Sign in")
    await google_sign_in(email, password, driver)

    driver.get(meet_link)

    driver.execute_cdp_cmd(
        "Browser.grantPermissions",
        {
            "origin": meet_link,
            "permissions": [
                "geolocation",
                "audioCapture",
                "displayCapture",
                "videoCapture",
                "videoCapturePanTiltZoom",
            ],
        },
    )

    print("screenshot")
    driver.save_screenshot("screenshots/initial.png")
    print("Done save initial")

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
        print("Done save allow microphone")
    except:
        print("No Allow Microphone popup")

    # if not missing_mic:
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
    print("Done save microphone")

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
    print("Done save camera")
    
    try:
        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[1]/div[3]/label/input',
        ).click()
        sleep(2)

        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[1]/div[3]/label/input',
        ).send_keys("TEST")
        sleep(2)
        driver.save_screenshot("screenshots/give_non_registered_name.png")

        print("Done save name")
        sleep(5)
        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[2]/div[1]/div[1]/button/span',
        ).click()
        sleep(5)
    except:
        print("authentification already done")
        sleep(5)
        # take screenshot
        driver.save_screenshot("screenshots/authentification_already_done.png")
        print(driver.title)

        driver.find_element(
            By.XPATH,
            '//*[@id="yDmH0d"]/c-wiz/div/div/div[14]/div[3]/div/div[2]/div[4]/div/div/div[2]/div[1]/div[2]/div[1]/div[1]/button',
        ).click()
        sleep(5)


    now = datetime.datetime.now()
    max_time = now + datetime.timedelta(
        minutes=os.getenv("MAX_WAITING_TIME_IN_MINUTES", 5)
    )

    joined = False
    while now < max_time and not joined:
        driver.save_screenshot("screenshots/joined.png")
        print("Done save joined")
        sleep(5)

        try:
            driver.find_element(
                By.XPATH,
                "/html/body/div[1]/div[3]/span/div[2]/div/div/div[2]/div[1]/button",
            ).click()
            driver.save_screenshot("screenshots/remove_popup.png")
            print("Done save popup in meeting")
        except:
            print("No popup in meeting")

        print("Try to click expand options")
        elements = driver.find_elements(By.CLASS_NAME, "VfPpkd-Bz112c-LgbsSe")
        expand_options = False
        for element in elements:
            if element.get_attribute("aria-label") == "More options":
                try:
                    element.click()
                    expand_options = True
                    print("Expand options clicked")
                except:
                    print("Not able to click expand options")

        driver.save_screenshot("screenshots/expand_options.png")

        sleep(2)
        print("Try to move to full screen")

        if expand_options:
            li_elements = driver.find_elements(
                By.CLASS_NAME, "V4jiNc.VfPpkd-StrnGf-rymPhb-ibnC6b"
            )
            for li_element in li_elements:
                txt = li_element.text.strip().lower()
                if "fullscreen" in txt:
                    li_element.click()
                    print("Full Screen clicked")
                    joined = True
                    break
                elif "minimize" in txt:
                    joined = True
                    break
                elif "close_fullscreen" in txt:
                    joined = True
                    break
                else:
                    pass

    duration_minutes = int(os.getenv("DURATION_IN_MINUTES", "15"))
    duration_seconds = duration_minutes * 60

    audio_streamer = RealtimeAudioStreamer(backend_url)
    transcript_listener = TranscriptListener(backend_url)

    print("Starting real-time audio streaming and transcription...")
    
    audio_streamer.start_realtime_streaming(duration_minutes)
    
    listener_task = asyncio.create_task(transcript_listener.start_listening())

    print("Start recording video")
    record_command = f"ffmpeg -y -video_size 1920x1080 -framerate 30 -f x11grab -i :99 -f pulse -i default -t {duration_seconds} -c:v libx264 -pix_fmt yuv420p -c:a aac -strict experimental recordings/output.mp4"

    await asyncio.gather(
        run_command_async(record_command),
    )

    print("Done recording")
    
    await audio_streamer.cleanup()
    transcript_listener.is_listening = False
    
    try:
        await asyncio.wait_for(listener_task, timeout=5.0)
    except asyncio.TimeoutError:
        print("Transcript listener timeout")

    print("- End of work")


if __name__ == "__main__":
    click.echo("Starting Google Meet recorder with real-time WebSocket streaming...")
    asyncio.run(join_meet())
    click.echo("Finished recording Google Meet.")