#!/bin/bash
# Test script for issue #1251: optional OpenAI-compatible speech-to-text and
# text-to-speech
#
# Test plan (each phase starts the real backend with a different environment):
# A. Nothing configured: both capabilities are reported as off and both
#    endpoints answer 404 (existing installations are unchanged)
# B. Both on, each with its own credential, against mocks/openai-audio-mock:
#    - /api/config/shell reports availability, defaults and limits, and no
#      base URL, model, voice or credential
#    - a WAV upload is transcribed; the mock received the configured model,
#      the credential, and a neutral filename
#    - text is synthesized and binary audio is relayed; the mock received the
#      configured model and voice, not the ones the client tried to send
#    - oversize audio, non-audio uploads and over-long text are refused
#    - the Content-Security-Policy allows blob: media
# C. Speech-to-text with a wrong credential (502) and text-to-speech pointed
#    at a slow server (504): provider errors and timeouts, and proof that the
#    two capabilities use separate endpoints and credentials
# D. Only speech-to-text on: transcription works, speech answers 404
# Then the backend unit test suite.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

WORK_DIR=$(mktemp -d -t atlas-pr1251-XXXXXX)
CONFIG_DIR="$WORK_DIR/config"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
echo '{}' > "$CONFIG_DIR/mcp.json"
MOCK_PID=""
SLOW_MOCK_PID=""
BACKEND_PID=""
trap 'kill $BACKEND_PID $MOCK_PID $SLOW_MOCK_PID 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

MOCK_PORT=8194
SLOW_MOCK_PORT=8195
PORT=8196
for p in $MOCK_PORT $SLOW_MOCK_PORT $PORT; do
    if curl -s "http://127.0.0.1:$p/" > /dev/null 2>&1; then
        echo "FAILED: Port $p is already in use by another process"
        exit 1
    fi
done
MOCK_URL="http://127.0.0.1:$MOCK_PORT"
SLOW_MOCK_URL="http://127.0.0.1:$SLOW_MOCK_PORT"
API="http://127.0.0.1:$PORT"
USER_HDR="X-User-Email: test@test.com"
STT_KEY="pr1251-stt-credential"
TTS_KEY="pr1251-tts-credential"

# A real WAV clip to upload, and a file that only claims to be one.
python3 - "$WORK_DIR" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("audio_mock", "mocks/openai-audio-mock/main.py")
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)
work = pathlib.Path(sys.argv[1])
(work / "clip.wav").write_bytes(mock.tone_wav(seconds=1.0))
(work / "long.wav").write_bytes(mock.tone_wav(seconds=10.0))
(work / "fake.wav").write_text("<html>this is not audio</html>")
PY

# --- Mock audio servers -----------------------------------------------------
print_header "Issue #1251: mock audio servers"
# One server stands in for both providers. It accepts either credential, so
# the checks below can tell from its request log which one each capability sent.
MOCK_AUDIO_PORT=$MOCK_PORT MOCK_AUDIO_TRANSCRIPT="validation transcript" \
    MOCK_AUDIO_KEY_LABELS="stt=$STT_KEY,tts=$TTS_KEY" \
    python mocks/openai-audio-mock/main.py > "$WORK_DIR/mock.log" 2>&1 &
MOCK_PID=$!
MOCK_AUDIO_PORT=$SLOW_MOCK_PORT MOCK_AUDIO_DELAY_SECONDS=5 MOCK_AUDIO_API_KEY="$TTS_KEY" \
    python mocks/openai-audio-mock/main.py > "$WORK_DIR/slow-mock.log" 2>&1 &
SLOW_MOCK_PID=$!
for i in $(seq 1 30); do
    curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && curl -sf "$SLOW_MOCK_URL/health" > /dev/null 2>&1 && break
    sleep 0.5
done
curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && curl -sf "$SLOW_MOCK_URL/health" > /dev/null 2>&1
print_result $? "Mock audio servers are up"

export ATLAS_HOST="127.0.0.1"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$LOG_DIR"
export USE_MOCK_S3=true
export DEBUG_MODE=true
# The debug sign-in is refused under ENVIRONMENT=production; do not inherit
# whatever the invoking shell has.
export ENVIRONMENT=development
export FEATURE_CHAT_HISTORY_ENABLED=false
export FEATURE_COMPLIANCE_LEVELS_ENABLED=false
# The run is self-contained: no .env is read, so the result does not depend on
# the developer's local settings. These two have no usable default.
export PYTHON_DOTENV_DISABLED=1
export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAPABILITY_TOKEN_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export PORT=$PORT

AUDIO_VARS="FEATURE_AUDIO_STT_ENABLED AUDIO_STT_BASE_URL AUDIO_STT_MODEL AUDIO_STT_API_KEY AUDIO_STT_MAX_AUDIO_BYTES FEATURE_AUDIO_TTS_ENABLED AUDIO_TTS_BASE_URL AUDIO_TTS_MODEL AUDIO_TTS_VOICE AUDIO_TTS_API_KEY AUDIO_TTS_RESPONSE_FORMAT AUDIO_TTS_TIMEOUT_SECONDS AUDIO_TTS_MAX_INPUT_CHARS"

stop_backend() {
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
        wait $BACKEND_PID 2>/dev/null || true
        BACKEND_PID=""
    fi
    unset $AUDIO_VARS
}

# start_backend NAME=VALUE ...   (the audio settings for this phase)
start_backend() {
    stop_backend
    for pair in "$@"; do
        export "$pair"
    done
    (cd "$ATLAS_DIR" && exec python main.py) > "$WORK_DIR/backend.log" 2>&1 &
    BACKEND_PID=$!
    for i in $(seq 1 90); do
        if ! kill -0 $BACKEND_PID 2>/dev/null; then
            echo "FAILED: Backend exited unexpectedly; log tail:"
            tail -30 "$WORK_DIR/backend.log"
            exit 1
        fi
        curl -sf "$API/api/health" > /dev/null 2>&1 && return 0
        sleep 1
    done
    echo "FAILED: Backend did not become ready; log tail:"
    tail -30 "$WORK_DIR/backend.log"
    exit 1
}

status_of() {
    curl -s -o /dev/null -w "%{http_code}" "$@"
}

transcribe_status() {
    status_of -H "$USER_HDR" -F "file=@$1;type=${2:-audio/wav}" "$API/api/audio/transcriptions"
}

speech_status() {
    status_of -H "$USER_HDR" -H "Content-Type: application/json" -d "$1" "$API/api/audio/speech"
}

# --- A. Nothing configured ---------------------------------------------------
print_header "A. Default configuration: both capabilities off"
start_backend
curl -s -H "$USER_HDR" "$API/api/config/shell" | python3 -c "
import sys, json
audio = json.load(sys.stdin)['audio']
assert audio == {'stt': {'enabled': False, 'show_button_by_default': True},
                 'tts': {'enabled': False, 'show_button_by_default': True}}, audio
print(audio)
"
print_result $? "Config reports both capabilities off by default"
[ "$(transcribe_status "$WORK_DIR/clip.wav")" = "404" ]
print_result $? "Transcription endpoint answers 404 while speech-to-text is off"
[ "$(speech_status '{"input": "hello"}')" = "404" ]
print_result $? "Speech endpoint answers 404 while text-to-speech is off"
curl -s -D - -o /dev/null -H "$USER_HDR" "$API/api/config/shell" | grep -i "^content-security-policy" | grep -qv "media-src"
print_result $? "Content-Security-Policy is unchanged while text-to-speech is off"
[ "$(curl -s "$MOCK_URL/mock/requests" | python3 -c "import sys, json; print(len(json.load(sys.stdin)['requests']))")" = "0" ]
print_result $? "Nothing reached the audio server"

# --- B. Both on ----------------------------------------------------------------
print_header "B. Both capabilities on, separate credentials"
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_BASE_URL="$MOCK_URL/v1" AUDIO_STT_MODEL=validation-whisper \
    AUDIO_STT_API_KEY="$STT_KEY" AUDIO_STT_MAX_AUDIO_BYTES=200000 \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_BASE_URL="$MOCK_URL/v1" AUDIO_TTS_MODEL=validation-tts \
    AUDIO_TTS_VOICE=validation-voice AUDIO_TTS_API_KEY="$TTS_KEY" AUDIO_TTS_RESPONSE_FORMAT=wav \
    AUDIO_TTS_MAX_INPUT_CHARS=200

curl -s -H "$USER_HDR" "$API/api/config/shell" > "$WORK_DIR/shell.json"
python3 - "$WORK_DIR/shell.json" "$STT_KEY" "$TTS_KEY" "$MOCK_PORT" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
audio = json.loads(raw)["audio"]
assert audio["stt"]["enabled"] is True and audio["tts"]["enabled"] is True, audio
assert audio["stt"]["max_audio_bytes"] == 200000, audio
assert audio["tts"]["max_input_chars"] == 200, audio
for hidden in (sys.argv[2], sys.argv[3], f":{sys.argv[4]}", "validation-whisper", "validation-tts", "validation-voice"):
    assert hidden not in raw, f"{hidden!r} leaked into the client config"
print(audio)
PY
print_result $? "Config reports availability and limits, and no provider details"

OUT=$(curl -s -H "$USER_HDR" -F "file=@$WORK_DIR/clip.wav;type=audio/wav;filename=my-private-notes.wav" -F "language=en" "$API/api/audio/transcriptions")
[ "$OUT" = '{"text":"validation transcript"}' ]
print_result $? "A WAV upload is transcribed ($OUT)"

curl -s -H "$USER_HDR" -H "Content-Type: application/json" \
    -d '{"input": "Read this sentence aloud.", "model": "attacker-model", "voice": "attacker-voice"}' \
    -D "$WORK_DIR/speech.headers" -o "$WORK_DIR/speech.wav" "$API/api/audio/speech"
python3 - "$WORK_DIR/speech.wav" "$WORK_DIR/speech.headers" <<'PY'
import sys, wave
headers = open(sys.argv[2]).read().lower()
assert "content-type: audio/wav" in headers, headers
assert "cache-control: no-store" in headers, headers
with wave.open(sys.argv[1]) as wav:
    assert wav.getnframes() > 0
print("audio/wav,", wav.getnframes(), "frames")
PY
print_result $? "Text is synthesized and playable binary audio is relayed"

curl -s "$MOCK_URL/mock/requests" | python3 -c "
import sys, json
stt, tts = json.load(sys.stdin)['requests']
assert stt['route'] == 'transcriptions' and stt['model'] == 'validation-whisper', stt
assert stt['language'] == 'en' and stt['filename'] == 'audio.wav' and stt['content_type'] == 'audio/wav', stt
assert stt['credential'] == 'stt', stt
assert tts['route'] == 'speech' and tts['model'] == 'validation-tts' and tts['voice'] == 'validation-voice', tts
assert tts['response_format'] == 'wav' and tts['credential'] == 'tts', tts
print(stt); print(tts)
"
print_result $? "The audio server got the configured model, voice and each capability's own credential"

[ "$(transcribe_status "$WORK_DIR/long.wav")" = "413" ]
print_result $? "Audio over AUDIO_STT_MAX_AUDIO_BYTES is refused with 413"
[ "$(transcribe_status "$WORK_DIR/fake.wav")" = "415" ]
print_result $? "A file that only claims to be audio is refused with 415"
[ "$(transcribe_status "$WORK_DIR/clip.wav" "text/plain")" = "415" ]
print_result $? "A non-audio content type is refused with 415"
LONG_TEXT=$(python3 -c "import json; print(json.dumps({'input': 'word ' * 100}))")
[ "$(speech_status "$LONG_TEXT")" = "413" ]
print_result $? "Text over AUDIO_TTS_MAX_INPUT_CHARS is refused with 413"
[ "$(speech_status '{"input": "   "}')" = "422" ]
print_result $? "Empty text is refused with 422"
[ "$(curl -s "$MOCK_URL/mock/requests" | python3 -c "import sys, json; print(len(json.load(sys.stdin)['requests']))")" = "2" ]
print_result $? "None of the refused requests reached the audio server"
curl -s -D - -o /dev/null -H "$USER_HDR" "$API/api/config/shell" | grep -i "^content-security-policy" | grep -q "media-src 'self' blob:"
print_result $? "Content-Security-Policy allows blob: media while text-to-speech is on"
! grep -q "$STT_KEY\|$TTS_KEY\|validation transcript\|Read this sentence aloud\|my-private-notes" "$WORK_DIR/backend.log" "$LOG_DIR"/* 2>/dev/null
print_result $? "No credential, transcript, text or filename in the backend logs"

# --- C. Provider failure and timeout -------------------------------------------
print_header "C. Wrong credential and a slow audio server"
curl -s -X DELETE "$MOCK_URL/mock/requests" > /dev/null
kill $MOCK_PID 2>/dev/null; wait $MOCK_PID 2>/dev/null
MOCK_AUDIO_PORT=$MOCK_PORT MOCK_AUDIO_API_KEY="$STT_KEY" \
    python mocks/openai-audio-mock/main.py > "$WORK_DIR/mock.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 30); do
    curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && break
    sleep 0.5
done
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_BASE_URL="$MOCK_URL/v1" AUDIO_STT_MODEL=validation-whisper \
    AUDIO_STT_API_KEY="not-the-right-credential" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_BASE_URL="$SLOW_MOCK_URL/v1" AUDIO_TTS_MODEL=validation-tts \
    AUDIO_TTS_VOICE=validation-voice AUDIO_TTS_API_KEY="$TTS_KEY" AUDIO_TTS_RESPONSE_FORMAT=wav \
    AUDIO_TTS_TIMEOUT_SECONDS=1
OUT=$(curl -s -w " %{http_code}" -H "$USER_HDR" -F "file=@$WORK_DIR/clip.wav;type=audio/wav" "$API/api/audio/transcriptions")
echo "$OUT" | grep -q " 502$" && echo "$OUT" | grep -q "rejected the configured credentials" && ! echo "$OUT" | grep -q "invalid api key"
print_result $? "A rejected credential is a 502 with an actionable message, not the provider's text"
OUT=$(curl -s -w " %{http_code}" -H "$USER_HDR" -H "Content-Type: application/json" -d '{"input": "hello"}' "$API/api/audio/speech")
echo "$OUT" | grep -q " 504$"
print_result $? "A speech request over AUDIO_TTS_TIMEOUT_SECONDS is a 504 ($OUT)"

# --- D. One capability on --------------------------------------------------------
print_header "D. Speech-to-text on, text-to-speech off"
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_BASE_URL="$MOCK_URL/v1" AUDIO_STT_MODEL=validation-whisper \
    AUDIO_STT_API_KEY="$STT_KEY"
[ "$(transcribe_status "$WORK_DIR/clip.wav")" = "200" ]
print_result $? "Transcription works on its own"
[ "$(speech_status '{"input": "hello"}')" = "404" ]
print_result $? "Speech answers 404 while only speech-to-text is on"
curl -s -H "$USER_HDR" "$API/api/config/shell" | python3 -c "
import sys, json
audio = json.load(sys.stdin)['audio']
assert audio['stt']['enabled'] is True and audio['tts']['enabled'] is False, audio
"
print_result $? "Config reports one capability on and the other off"
stop_backend

# --- Backend unit tests ----------------------------------------------------------
print_header "Backend unit tests"
"$PROJECT_ROOT/test/run_tests.sh" backend > "$WORK_DIR/unit.log" 2>&1
RC=$?
tail -3 "$WORK_DIR/unit.log"
print_result $RC "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
