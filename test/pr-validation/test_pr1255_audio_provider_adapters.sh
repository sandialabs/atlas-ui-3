#!/bin/bash
# Test script for issue #1255: audio provider adapters (LiteLLM in-process,
# Google Gemini, ElevenLabs, OpenAI Chat Completions audio) for speech-to-text
# and text-to-speech
#
# Test plan (each phase starts the real backend with a different environment,
# against mocks/openai-audio-mock, which also answers the provider-native routes):
# A. No provider settings: the #1251 openai_compatible behavior is unchanged,
#    and no instructions/speed fields are sent
# B. AUDIO_*_PROVIDER=litellm: real in-process litellm.atranscription() /
#    litellm.aspeech() calls (no LiteLLM proxy) reach the mock; instructions and
#    speed are passed through
# C. gemini: generateContent with x-goog-api-key taken from GOOGLE_API_KEY;
#    the returned raw PCM is relayed as a playable WAV file
# D. elevenlabs: native routes with xi-api-key from ELEVENLABS_API_KEY; wav
#    is requested as PCM and framed
# E. openai_chat_audio: /chat/completions audio; dictation takes WAV, refuses
#    browser WebM with 415 before contacting the server; the browser config
#    lists only WAV/MP3 as accepted (so the UI hides the microphone); a bad
#    AUDIO_TTS_SPEED, which this provider never sends, leaves read-aloud on
# F. Mixed providers (litellm STT + gemini TTS) are independent; a provider
#    missing its key, or an unknown provider, is reported off and answers 404
# G. docker-compose.yml forwards every credential fallback the providers read
# Every phase: no credential, transcript or text in the backend logs, and the
# browser config never names the provider, URL, model or voice.
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

WORK_DIR=$(mktemp -d -t atlas-pr1255-XXXXXX)
CONFIG_DIR="$WORK_DIR/config"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
echo '{}' > "$CONFIG_DIR/mcp.json"
MOCK_PID=""
BACKEND_PID=""
trap 'kill $BACKEND_PID $MOCK_PID 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

MOCK_PORT=8197
PORT=8198
for p in $MOCK_PORT $PORT; do
    if curl -s "http://127.0.0.1:$p/" > /dev/null 2>&1; then
        echo "FAILED: Port $p is already in use by another process"
        exit 1
    fi
done
MOCK_URL="http://127.0.0.1:$MOCK_PORT"
API="http://127.0.0.1:$PORT"
USER_HDR="X-User-Email: test@test.com"
STT_KEY="pr1255-stt-credential"
TTS_KEY="pr1255-tts-credential"
SECRET_TEXT="Read this confidential sentence aloud."

python3 - "$WORK_DIR" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("audio_mock", "mocks/openai-audio-mock/main.py")
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)
work = pathlib.Path(sys.argv[1])
(work / "clip.wav").write_bytes(mock.tone_wav(seconds=1.0))
(work / "clip.webm").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 256)
PY

print_header "Issue #1255: mock audio server"
MOCK_AUDIO_PORT=$MOCK_PORT MOCK_AUDIO_TRANSCRIPT="validation transcript" \
    MOCK_AUDIO_KEY_LABELS="stt=$STT_KEY,tts=$TTS_KEY" \
    python mocks/openai-audio-mock/main.py > "$WORK_DIR/mock.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 30); do
    curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && break
    sleep 0.5
done
curl -sf "$MOCK_URL/health" > /dev/null 2>&1
print_result $? "Mock audio server is up"

export ATLAS_HOST="127.0.0.1"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$LOG_DIR"
export USE_MOCK_S3=true
export DEBUG_MODE=true
export ENVIRONMENT=development
export FEATURE_CHAT_HISTORY_ENABLED=false
export FEATURE_COMPLIANCE_LEVELS_ENABLED=false
export PYTHON_DOTENV_DISABLED=1
export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAPABILITY_TOKEN_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export PORT=$PORT

AUDIO_VARS="FEATURE_AUDIO_STT_ENABLED AUDIO_STT_PROVIDER AUDIO_STT_BASE_URL AUDIO_STT_MODEL AUDIO_STT_API_KEY FEATURE_AUDIO_TTS_ENABLED AUDIO_TTS_PROVIDER AUDIO_TTS_BASE_URL AUDIO_TTS_MODEL AUDIO_TTS_VOICE AUDIO_TTS_API_KEY AUDIO_TTS_RESPONSE_FORMAT AUDIO_TTS_INSTRUCTIONS AUDIO_TTS_SPEED GOOGLE_API_KEY GEMINI_API_KEY ELEVENLABS_API_KEY"
# Provider keys from the invoking shell must not leak into a phase.
unset $AUDIO_VARS

stop_backend() {
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
        wait $BACKEND_PID 2>/dev/null || true
        BACKEND_PID=""
    fi
    unset $AUDIO_VARS
}

start_backend() {
    stop_backend
    for pair in "$@"; do
        export "$pair"
    done
    (cd "$ATLAS_DIR" && exec python main.py) >> "$WORK_DIR/backend.log" 2>&1 &
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

clear_mock() {
    curl -s -X DELETE "$MOCK_URL/mock/requests" > /dev/null
}

# mock_requests PYTHON_ASSERTIONS   ('reqs' is the list the mock received)
mock_requests() {
    curl -s "$MOCK_URL/mock/requests" | python3 -c "
import sys, json
reqs = json.load(sys.stdin)['requests']
$1
print(reqs)
"
}

transcribe() {
    curl -s -w " %{http_code}" -H "$USER_HDR" -F "file=@$1;type=${2:-audio/wav}" -F "language=en" "$API/api/audio/transcriptions"
}

# speak OUTPUT_FILE -> prints the status code; headers in OUTPUT_FILE.headers
speak() {
    curl -s -o "$1" -D "$1.headers" -w "%{http_code}" -H "$USER_HDR" -H "Content-Type: application/json" \
        -d "{\"input\": \"$SECRET_TEXT\"}" "$API/api/audio/speech"
}

# check_wav FILE [EXPECTED_RATE]
check_wav() {
    python3 - "$1" "${2:-0}" <<'PY'
import sys, wave
headers = open(sys.argv[1] + ".headers").read().lower()
assert "content-type: audio/wav" in headers, headers
assert "cache-control: no-store" in headers, headers
with wave.open(sys.argv[1]) as wav:
    assert wav.getnframes() > 0 and wav.getsampwidth() == 2 and wav.getnchannels() == 1
    if int(sys.argv[2]):
        assert wav.getframerate() == int(sys.argv[2]), wav.getframerate()
    print("audio/wav,", wav.getnframes(), "frames at", wav.getframerate(), "Hz")
PY
}

check_client_config() {
    curl -s -H "$USER_HDR" "$API/api/config/shell" > "$WORK_DIR/shell.json"
    python3 - "$WORK_DIR/shell.json" "$MOCK_PORT" "$@" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
audio = json.loads(raw)["audio"]
assert audio["stt"]["enabled"] is True and audio["tts"]["enabled"] is True, audio
# The mock's address and the credentials must not appear anywhere; provider,
# model and voice names are checked in the audio section only, because the
# shell config also lists the chat models (some of which are Gemini models).
for hidden in (f":{sys.argv[2]}", "pr1255-stt-credential", "pr1255-tts-credential"):
    assert hidden not in raw, f"{hidden!r} leaked into the client config"
audio_raw = json.dumps(audio)
for hidden in sys.argv[3:]:
    assert hidden not in audio_raw, f"{hidden!r} leaked into the audio config"
print(audio)
PY
}

# --- A. Default provider -------------------------------------------------------
print_header "A. No provider settings: openai_compatible, unchanged"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_BASE_URL="$MOCK_URL/v1" AUDIO_STT_MODEL=validation-whisper \
    AUDIO_STT_API_KEY="$STT_KEY" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_BASE_URL="$MOCK_URL/v1" AUDIO_TTS_MODEL=validation-tts \
    AUDIO_TTS_VOICE=validation-voice AUDIO_TTS_API_KEY="$TTS_KEY" AUDIO_TTS_RESPONSE_FORMAT=wav
check_client_config validation-whisper validation-tts validation-voice openai_compatible
print_result $? "Config reports both on and names no provider, URL, model or voice"
[ "$(transcribe "$WORK_DIR/clip.wav")" = '{"text":"validation transcript"} 200' ]
print_result $? "Dictation works through /audio/transcriptions"
[ "$(speak "$WORK_DIR/a.wav")" = "200" ] && check_wav "$WORK_DIR/a.wav"
print_result $? "Read-aloud works through /audio/speech"
mock_requests "
assert [r['route'] for r in reqs] == ['transcriptions', 'speech'], reqs
assert reqs[1]['extra_fields'] == [], reqs[1]
assert [r['credential'] for r in reqs] == ['stt', 'tts'], reqs
"
print_result $? "The speech request carries exactly the #1251 fields (no instructions or speed)"
python3 -c "
import json
types = json.load(open('$WORK_DIR/shell.json'))['audio']['stt']['accepted_media_types']
assert {'audio/webm', 'audio/mp4', 'audio/ogg'} <= set(types), types
print(types)
"
print_result $? "The default provider lists the browser recording formats as accepted"

# --- B. LiteLLM in process -------------------------------------------------------
print_header "B. AUDIO_*_PROVIDER=litellm: in-process LiteLLM, no proxy"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=litellm AUDIO_STT_BASE_URL="$MOCK_URL/v1" \
    AUDIO_STT_MODEL=openai/validation-whisper AUDIO_STT_API_KEY="$STT_KEY" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=litellm AUDIO_TTS_BASE_URL="$MOCK_URL/v1" \
    AUDIO_TTS_MODEL=openai/validation-tts AUDIO_TTS_VOICE=validation-voice AUDIO_TTS_API_KEY="$TTS_KEY" \
    AUDIO_TTS_RESPONSE_FORMAT=wav AUDIO_TTS_INSTRUCTIONS="Speak warmly." AUDIO_TTS_SPEED=1.25
OUT=$(transcribe "$WORK_DIR/clip.wav")
[ "$OUT" = '{"text":"validation transcript"} 200' ]
print_result $? "litellm.atranscription() transcribes ($OUT)"
[ "$(speak "$WORK_DIR/b.wav")" = "200" ] && check_wav "$WORK_DIR/b.wav"
print_result $? "litellm.aspeech() synthesizes playable audio"
mock_requests "
assert [r['route'] for r in reqs] == ['transcriptions', 'speech'], reqs
assert reqs[0]['model'] == 'validation-whisper' and reqs[0]['credential'] == 'stt', reqs[0]
assert reqs[1]['model'] == 'validation-tts' and reqs[1]['voice'] == 'validation-voice', reqs[1]
assert {'instructions', 'speed'} <= set(reqs[1]['extra_fields']) and reqs[1]['credential'] == 'tts', reqs[1]
"
print_result $? "LiteLLM sent the model (prefix stripped), voice, instructions, speed and each credential"

# --- C. Gemini -----------------------------------------------------------------------
print_header "C. AUDIO_*_PROVIDER=gemini with GOOGLE_API_KEY"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=gemini AUDIO_STT_BASE_URL="$MOCK_URL/v1beta" \
    AUDIO_STT_MODEL=gemini-validation-flash \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=gemini AUDIO_TTS_BASE_URL="$MOCK_URL/v1beta" \
    AUDIO_TTS_MODEL=gemini-validation-tts AUDIO_TTS_VOICE=Kore GOOGLE_API_KEY="$TTS_KEY"
check_client_config gemini Kore googleapis
print_result $? "Config does not reveal the Gemini provider or voice"
[ "$(transcribe "$WORK_DIR/clip.wav")" = '{"text":"validation transcript"} 200' ]
print_result $? "Gemini generateContent transcribes"
[ "$(speak "$WORK_DIR/c.wav")" = "200" ] && check_wav "$WORK_DIR/c.wav" 16000
print_result $? "Gemini PCM is relayed as playable WAV at the declared sample rate"
mock_requests "
assert [r['route'] for r in reqs] == ['gemini_transcription', 'gemini_speech'], reqs
assert reqs[0]['model'] == 'gemini-validation-flash' and reqs[0]['mime_type'] == 'audio/wav', reqs[0]
assert reqs[1]['voice'] == 'Kore', reqs[1]
assert all(r['credential'] == 'tts' for r in reqs), reqs
"
print_result $? "Gemini got the model, voice and GOOGLE_API_KEY in x-goog-api-key"

# --- D. ElevenLabs -------------------------------------------------------------------
print_header "D. AUDIO_*_PROVIDER=elevenlabs with ELEVENLABS_API_KEY"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=elevenlabs AUDIO_STT_BASE_URL="$MOCK_URL/v1" \
    AUDIO_STT_MODEL=scribe_v2 AUDIO_STT_API_KEY="$STT_KEY" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=elevenlabs AUDIO_TTS_BASE_URL="$MOCK_URL/v1" \
    AUDIO_TTS_MODEL=eleven_multilingual_v2 AUDIO_TTS_VOICE=validationVoiceId AUDIO_TTS_RESPONSE_FORMAT=wav \
    ELEVENLABS_API_KEY="$TTS_KEY"
[ "$(transcribe "$WORK_DIR/clip.wav")" = '{"text":"validation transcript"} 200' ]
print_result $? "ElevenLabs speech-to-text transcribes"
[ "$(speak "$WORK_DIR/d.wav")" = "200" ] && check_wav "$WORK_DIR/d.wav" 24000
print_result $? "ElevenLabs PCM is framed as playable WAV"
mock_requests "
assert [r['route'] for r in reqs] == ['elevenlabs_transcription', 'elevenlabs_speech'], reqs
assert reqs[0]['model'] == 'scribe_v2' and reqs[0]['language'] == 'en' and reqs[0]['credential'] == 'stt', reqs[0]
assert reqs[1]['voice'] == 'validationVoiceId' and reqs[1]['output_format'] == 'pcm_24000', reqs[1]
assert reqs[1]['credential'] == 'tts', reqs[1]
"
print_result $? "ElevenLabs got model_id, the voice in the path and xi-api-key"

# --- E. Chat Completions audio ------------------------------------------------------------
print_header "E. AUDIO_*_PROVIDER=openai_chat_audio"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=openai_chat_audio AUDIO_STT_BASE_URL="$MOCK_URL/v1" \
    AUDIO_STT_MODEL=validation-audio-chat AUDIO_STT_API_KEY="$STT_KEY" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=openai_chat_audio AUDIO_TTS_BASE_URL="$MOCK_URL/v1" \
    AUDIO_TTS_MODEL=validation-audio-chat AUDIO_TTS_VOICE=alloy AUDIO_TTS_API_KEY="$TTS_KEY" \
    AUDIO_TTS_RESPONSE_FORMAT=wav AUDIO_TTS_SPEED=fast
check_client_config openai_chat_audio alloy validation-audio-chat && python3 -c "
import json
audio = json.load(open('$WORK_DIR/shell.json'))['audio']
assert audio['stt']['accepted_media_types'] == ['audio/mpeg', 'audio/wav'], audio['stt']
"
print_result $? "Config lists only WAV/MP3 uploads (the UI hides the mic) and a bad AUDIO_TTS_SPEED leaves read-aloud on"
[ "$(transcribe "$WORK_DIR/clip.wav")" = '{"text":"validation transcript"} 200' ]
print_result $? "Chat Completions audio transcribes a WAV upload"
OUT=$(transcribe "$WORK_DIR/clip.webm" "audio/webm")
echo "$OUT" | grep -q " 415$" && echo "$OUT" | grep -q "does not accept this audio format"
print_result $? "A WebM recording is refused with 415 ($OUT)"
[ "$(speak "$WORK_DIR/e.wav")" = "200" ] && check_wav "$WORK_DIR/e.wav"
print_result $? "Chat Completions audio synthesizes playable WAV"
mock_requests "
assert [r['route'] for r in reqs] == ['chat_transcription', 'chat_speech'], reqs
assert reqs[0]['format'] == 'wav' and reqs[0]['credential'] == 'stt', reqs[0]
assert reqs[1]['voice'] == 'alloy' and reqs[1]['format'] == 'wav' and reqs[1]['credential'] == 'tts', reqs[1]
"
print_result $? "The refused WebM upload never reached the server; the others carried model, voice, format and key"

# --- F. Mixed and misconfigured ---------------------------------------------------------
print_header "F. Independent providers, and misconfiguration"
clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=litellm AUDIO_STT_BASE_URL="$MOCK_URL/v1" \
    AUDIO_STT_MODEL=openai/validation-whisper AUDIO_STT_API_KEY="$STT_KEY" \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=gemini AUDIO_TTS_BASE_URL="$MOCK_URL/v1beta" \
    AUDIO_TTS_MODEL=gemini-validation-tts GEMINI_API_KEY="$TTS_KEY"
[ "$(transcribe "$WORK_DIR/clip.wav")" = '{"text":"validation transcript"} 200' ] && [ "$(speak "$WORK_DIR/f.wav")" = "200" ]
print_result $? "LiteLLM dictation and Gemini read-aloud run side by side"
mock_requests "assert [r['route'] for r in reqs] == ['transcriptions', 'gemini_speech'], reqs"
print_result $? "Each capability reached its own provider"

clear_mock
start_backend \
    FEATURE_AUDIO_STT_ENABLED=true AUDIO_STT_PROVIDER=gemini AUDIO_STT_BASE_URL="$MOCK_URL/v1beta" \
    AUDIO_STT_MODEL=gemini-validation-flash \
    FEATURE_AUDIO_TTS_ENABLED=true AUDIO_TTS_PROVIDER=no_such_provider AUDIO_TTS_BASE_URL="$MOCK_URL/v1" \
    AUDIO_TTS_MODEL=validation-tts AUDIO_TTS_VOICE=validation-voice
curl -s -H "$USER_HDR" "$API/api/config/shell" | python3 -c "
import sys, json
audio = json.load(sys.stdin)['audio']
assert audio['stt']['enabled'] is False and audio['tts']['enabled'] is False, audio
print(audio)
"
print_result $? "Gemini without a key and an unknown provider are both reported off"
[ "$(transcribe "$WORK_DIR/clip.wav" | awk '{print $NF}')" = "404" ] && [ "$(speak "$WORK_DIR/g.wav")" = "404" ]
print_result $? "Both endpoints answer 404"
grep -q "the gemini provider needs a credential" "$WORK_DIR/backend.log" && grep -q "AUDIO_TTS_PROVIDER must be one of" "$WORK_DIR/backend.log"
print_result $? "The reason each capability is off is logged for the operator"
mock_requests "assert reqs == [], reqs"
print_result $? "Nothing reached the audio server"
stop_backend

! grep -q "$STT_KEY\|$TTS_KEY\|validation transcript\|$SECRET_TEXT\|Speak warmly" "$WORK_DIR/backend.log" "$LOG_DIR"/* 2>/dev/null
print_result $? "No credential, transcript, text or instructions in the backend logs (all phases)"

# --- G. docker-compose credential fallbacks -------------------------------------------
print_header "G. docker-compose.yml forwards the provider credential fallbacks"
python3 - "$PROJECT_ROOT" <<'PY'
import sys, pathlib
sys.path.insert(0, sys.argv[1])
from atlas.modules.audio.audio_providers import PROVIDERS
compose = (pathlib.Path(sys.argv[1]) / "docker-compose.yml").read_text()
example = (pathlib.Path(sys.argv[1]) / ".env.example").read_text()
names = sorted({n for spec in PROVIDERS.values() for n in spec.fallback_env_names})
missing = [n for n in names if f"- {n}=${{{n}:-}}" not in compose or n not in example]
assert not missing, missing
print(names)
PY
print_result $? "Every fallback credential variable is forwarded by compose and listed in .env.example"

# --- Backend unit tests ---------------------------------------------------------------
print_header "Backend unit tests"
"$PROJECT_ROOT/test/run_tests.sh" backend > "$WORK_DIR/unit.log" 2>&1
RC=$?
tail -3 "$WORK_DIR/unit.log"
print_result $RC "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
