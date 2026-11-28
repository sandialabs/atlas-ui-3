# Mock OpenAI-Compatible Audio Server

A stand-in for a speech server (Speaches, a LiteLLM proxy, a hosted API) so
dictation and read-aloud can be developed and tested without a model. It
implements the two routes Atlas calls:

| Route | Request | Reply |
| ----- | ------- | ----- |
| `POST /v1/audio/transcriptions` | multipart `file`, `model`, optional `language` | `{"text": "..."}` |
| `POST /v1/audio/speech` | JSON `model`, `input`, `voice`, `response_format` | a WAV tone (`audio/wav`) |

It also answers the provider-native routes the issue #1255 adapters call,
with the same canned transcript and tone:

| Route | Provider setting | Reply |
| ----- | ---------------- | ----- |
| `POST /v1beta/models/{model}:generateContent` | `gemini` (base URL `http://127.0.0.1:8094/v1beta`) | transcript text, or base64 raw PCM when `responseModalities` is `["AUDIO"]` |
| `POST /v1/speech-to-text` | `elevenlabs` | `{"text": "..."}` |
| `POST /v1/text-to-speech/{voice_id}` | `elevenlabs` | a WAV tone, or raw PCM for a `pcm_*` `output_format` |
| `POST /v1/chat/completions` | `openai_chat_audio` | transcript text, or a base64 WAV tone in `message.audio` |

The `litellm` provider can use the first two routes through LiteLLM's
OpenAI handler: `AUDIO_*_PROVIDER=litellm`, a model such as
`openai/mock-whisper`, and the base URL below.

`GET /mock/requests` lists what was received (field values, sizes and, when
`MOCK_AUDIO_KEY_LABELS` is set, the label of the credential that arrived;
never the credential or anything derived from it, the audio or the text), and
`DELETE /mock/requests` clears the list.

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
| `MOCK_AUDIO_API_KEY` | Require the key (`Authorization: Bearer <key>`, or `x-goog-api-key` / `xi-api-key` on the provider-native routes); pair with `AUDIO_STT_API_KEY` / `AUDIO_TTS_API_KEY` |
| `MOCK_AUDIO_KEY_LABELS` | `name=key,name=key`: each logged request gets `credential` set to the matching name, `unrecognized`, or `null` when none was sent |
| `MOCK_AUDIO_DELAY_SECONDS` | Delay every reply, to exercise `AUDIO_*_TIMEOUT_SECONDS` |
| `MOCK_AUDIO_TRANSCRIPT` | The text every transcription returns |

Operator documentation for the feature is in
[docs/admin/audio-stt-tts.md](../../docs/admin/audio-stt-tts.md).
