# Mock OpenAI-Compatible Audio Server

A stand-in for a speech server (Speaches, a LiteLLM proxy, a hosted API) so
dictation and read-aloud can be developed and tested without a model. It
implements the two routes Atlas calls:

| Route | Request | Reply |
| ----- | ------- | ----- |
| `POST /v1/audio/transcriptions` | multipart `file`, `model`, optional `language` | `{"text": "..."}` |
| `POST /v1/audio/speech` | JSON `model`, `input`, `voice`, `response_format` | a WAV tone (`audio/wav`) |

`GET /mock/requests` lists what was received (field values, the
`Authorization` header and sizes, never the audio or text), and `DELETE /mock/requests` clears the list.

## Try it

```bash
python mocks/openai-audio-mock/main.py &          # port 8094
```

Then point Atlas at it in `.env` and restart:

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_BASE_URL=http://127.0.0.1:8094/v1
AUDIO_STT_MODEL=mock-whisper

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_BASE_URL=http://127.0.0.1:8094/v1
AUDIO_TTS_MODEL=mock-tts
AUDIO_TTS_VOICE=mock-voice
AUDIO_TTS_RESPONSE_FORMAT=wav
```

The microphone button returns a fixed sentence and the read-aloud button
plays a tone whose length grows with the text, which is enough to try pause,
stop and playback speed.

## Options

| Variable | Effect |
| -------- | ------ |
| `MOCK_AUDIO_PORT` | Port to listen on (default `8094`) |
| `MOCK_AUDIO_API_KEY` | Require `Authorization: Bearer <key>`; pair with `AUDIO_STT_API_KEY` / `AUDIO_TTS_API_KEY` |
| `MOCK_AUDIO_DELAY_SECONDS` | Delay every reply, to exercise `AUDIO_*_TIMEOUT_SECONDS` |
| `MOCK_AUDIO_TRANSCRIPT` | The text every transcription returns |

Operator documentation for the feature is in
[docs/admin/audio-stt-tts.md](../../docs/admin/audio-stt-tts.md).
