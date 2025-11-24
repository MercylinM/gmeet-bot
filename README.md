# gmeet-bot

This project records and transcribes using Google Cloud Speech to Text a google meet meeting from a container with a audio and video using virtual sound card (pulseaudio) and screen recording with Xscreen.

One of the main challenge is to record the session without a sound card using audio loop sink and not being flagged by the meeting provider (in this case google meet).

## Build

```
docker build -t gmeet -f Dockerfile .
```

## Usage

```
<docker run -it \
    -e GMEET_LINK=https://meet.google.com/my-gmeet-id \
    -e GMAIL_USER_EMAIL=linmercynj@gmail.com \
    -e GMAIL_USER_PASSWORD=tYpiWWz!!68r$BM \
    -e DURATION_IN_MINUTES=1 \ 
    -e MAX_WAIT_TIME_IN_MINUTES=2 \ 
    -v $PWD/recordings:/app/recordings \ 
    -v $PWD/screenshots:/app/screenshots \ 
    gmeet>
```
