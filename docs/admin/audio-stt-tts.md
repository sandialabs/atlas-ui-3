# Dictation and Read-Aloud (Speech-to-Text and Text-to-Speech)

Last updated: 2026-11-27

Atlas can transcribe a spoken prompt (a microphone button in the message box)
and read an assistant reply aloud (a speaker button on the reply). Each is an
optional capability backed by an audio service that you configure. By
default that is an OpenAI-compatible endpoint; each capability can instead use
one of several provider adapters (see [Providers](#providers)):

| Capability | Atlas endpoint | Upstream request |
| ---------- | -------------- | ---------------- |
| Speech-to-text (dictation) | `POST /api/audio/transcriptions` | With the default provider: `POST {AUDIO_STT_BASE_URL}/audio/transcriptions` (multipart upload, JSON reply) |
| Text-to-speech (read-aloud) | `POST /api/audio/speech` | With the default provider: `POST {AUDIO_TTS_BASE_URL}/audio/speech` (JSON request, binary audio reply) |

The two are independent of each other and of the chat models. They do not use
`llmconfig.yml`; each has its own provider, base URL, model and credential, so
they can point at different servers, at one self-hosted server, at a gateway,
or at different hosted services. The default provider, `openai_compatible`,
works with any server that implements the two OpenAI-style routes above.
Setting `AUDIO_STT_PROVIDER` or `AUDIO_TTS_PROVIDER` selects a different
adapter for that capability only; one of them (`litellm`) calls the LiteLLM
library in process, with no LiteLLM proxy server involved.

Both are **off by default**. An installation that sets none of the variables
below behaves exactly as before.

## Contents

- [Configuration](#configuration)
- [Providers](#providers)
- [Button visibility: administrator default and user preference](#button-visibility-administrator-default-and-user-preference)
- [Example: self-hosted Speaches](#example-self-hosted-speaches)
- [Example: through a LiteLLM proxy or another gateway](#example-through-a-litellm-proxy-or-another-gateway)
- [Access control and data classification](#access-control-and-data-classification)
- [Limits and timeouts](#limits-and-timeouts)
- [Privacy, logging and retention](#privacy-logging-and-retention)
- [Browser requirements](#browser-requirements)
- [Content Security Policy](#content-security-policy)
- [Errors](#errors)
- [Trying it without a speech model](#trying-it-without-a-speech-model)

## Configuration

Set these in `.env` (or the process environment) and restart Atlas. The
credentials are read from the environment like the other Atlas secrets; they
are never sent to the browser and are masked in the admin configuration view.

### Speech-to-text

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `FEATURE_AUDIO_STT_ENABLED` | `false` | Turns dictation on |
| `AUDIO_STT_PROVIDER` | `openai_compatible` | Adapter used for dictation: `openai_compatible`, `litellm`, `gemini`, `elevenlabs` or `openai_chat_audio`; see [Providers](#providers) |
| `AUDIO_STT_BASE_URL` | (none) | Base URL of the audio API, including its version prefix, e.g. `http://speaches:8000/v1`. Optional for `litellm`; defaulted for `gemini` and `elevenlabs` |
| `AUDIO_STT_MODEL` | (none) | Model identifier sent as `model` (`model_id` for ElevenLabs; `litellm` models carry a provider prefix) |
| `AUDIO_STT_API_KEY` | (none) | Optional for the default provider (sent as `Authorization: Bearer <key>`). How it is sent, and the fallback environment variables, depend on the provider |
| `AUDIO_STT_SHOW_BUTTON_BY_DEFAULT` | `true` | Whether users who have not chosen see the microphone button |
| `AUDIO_STT_GROUPS` | (empty) | Comma-separated groups allowed to dictate. Empty allows every signed-in user |
| `AUDIO_STT_ALLOWED_DATA_CLASSIFICATIONS` | (empty) | Compliance levels the endpoint is approved for; see [below](#access-control-and-data-classification) |
| `AUDIO_STT_TIMEOUT_SECONDS` | `60` | Overall deadline for one transcription |
| `AUDIO_STT_MAX_AUDIO_BYTES` | `26214400` (25 MB) | Largest upload accepted |
| `AUDIO_STT_MAX_DURATION_SECONDS` | `300` | Longest recording the microphone button makes |

### Text-to-speech

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `FEATURE_AUDIO_TTS_ENABLED` | `false` | Turns read-aloud on |
| `AUDIO_TTS_PROVIDER` | `openai_compatible` | Adapter used for read-aloud; same choices as `AUDIO_STT_PROVIDER` except that it is chosen independently |
| `AUDIO_TTS_BASE_URL` | (none) | Base URL of the audio API, e.g. `http://speaches:8000/v1`. Optional for `litellm`; defaulted for `gemini` and `elevenlabs` |
| `AUDIO_TTS_MODEL` | (none) | Model identifier sent as `model` (`model_id` for ElevenLabs) |
| `AUDIO_TTS_VOICE` | (none) | Voice identifier sent as `voice`. For `elevenlabs` it is the voice ID; for `gemini` a prebuilt voice name such as `Kore`; optional for `litellm` and `gemini` |
| `AUDIO_TTS_API_KEY` | (none) | Optional for the default provider (sent as `Authorization: Bearer <key>`). How it is sent, and the fallback environment variables, depend on the provider |
| `AUDIO_TTS_SHOW_BUTTON_BY_DEFAULT` | `true` | Whether users who have not chosen see the read-aloud button |
| `AUDIO_TTS_GROUPS` | (empty) | Comma-separated groups allowed to use read-aloud |
| `AUDIO_TTS_ALLOWED_DATA_CLASSIFICATIONS` | (empty) | Compliance levels the endpoint is approved for |
| `AUDIO_TTS_RESPONSE_FORMAT` | `mp3` | Format requested from the server: `mp3`, `wav`, `opus`, `aac` or `flac`. Some providers accept fewer; `gemini` ignores it and always returns WAV |
| `AUDIO_TTS_INSTRUCTIONS` | (empty) | Optional delivery direction (tone, pace). Used by `openai_compatible` (sent as `instructions` only when set), `litellm`, `gemini` and `openai_chat_audio`; ignored by `elevenlabs` |
| `AUDIO_TTS_SPEED` | (empty) | Optional speaking speed, a number from 0.25 to 4.0; empty means unset. Used by `openai_compatible` (sent as `speed` only when set), `litellm` and `elevenlabs`; ignored (and not checked) for `gemini` and `openai_chat_audio`. For the providers that use it, a value that is not a number in range switches read-aloud off, with the reason logged |
| `AUDIO_TTS_TIMEOUT_SECONDS` | `60` | Overall deadline for one speech request |
| `AUDIO_TTS_MAX_INPUT_CHARS` | `4096` | Longest text sent in one request |
| `AUDIO_TTS_MAX_AUDIO_BYTES` | `26214400` (25 MB) | Largest audio reply relayed to the browser |

### Shared

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `AUDIO_MAX_CONCURRENT_REQUESTS` | `8` | Audio requests (both kinds together) that one backend process relays at once. Further requests get 503 with `Retry-After` |

The bundled `docker-compose.yml` passes every one of these variables through
to the `atlas-ui` service from your shell environment or the `.env` file next
to it, with the defaults above, so they are set the same way there.

A capability that is switched on but incompletely configured (an unknown
provider, no base URL where the provider needs one, a base URL that is not a
well-formed `http(s)` URL, no model, no credential for a provider that
requires one, no voice where the provider requires one, an unknown or
unsupported response format, or an invalid `AUDIO_TTS_SPEED`) stays **off**: its button is hidden and its
endpoint answers 404. The reason is written to the application log once, as
`FEATURE_AUDIO_STT_ENABLED is true but the capability stays off: ...`. A
mistake in one capability's settings never affects the other capability or
the rest of Atlas.

The provider, the model, the voice and the endpoint are chosen by the
administrator only. A request from the browser cannot name a different one.

## Providers

`AUDIO_STT_PROVIDER` and `AUDIO_TTS_PROVIDER` each pick one adapter, and the
two are independent: dictation can use one service and read-aloud another.
The default, `openai_compatible`, is the behavior described in the examples
further down and is unchanged. A provider name Atlas does not know, or a
provider whose required credential is missing, leaves that capability **off**
(its button is hidden and its endpoint answers 404) and logs the reason once.

| Provider | What Atlas calls | Credential | Base URL | Voice | TTS formats | Instructions / speed | Dictation uploads |
| -------- | ---------------- | ---------- | -------- | ----- | ----------- | -------------------- | ----------------- |
| `openai_compatible` (default) | `/audio/transcriptions` and `/audio/speech` | `AUDIO_*_API_KEY`, optional, as a Bearer token | Required | Required (TTS) | mp3, wav, opus, aac, flac | Both sent only when set | All accepted types |
| `litellm` | `litellm.atranscription()` / `litellm.aspeech()` in process | `AUDIO_*_API_KEY`, optional; otherwise the provider's own variable (`GROQ_API_KEY`, `ELEVENLABS_API_KEY`, ...) | Optional override | Optional | mp3, wav, opus, aac, flac (as the provider allows) | Both passed through | All accepted types |
| `gemini` | `POST {base}/models/{model}:generateContent` | `AUDIO_*_API_KEY`, else `GOOGLE_API_KEY`, else `GEMINI_API_KEY`; sent as `x-goog-api-key` | `https://generativelanguage.googleapis.com/v1beta` | Optional (prebuilt voice name) | Always WAV | Instructions yes, speed no | All accepted types, inline up to about 20 MB |
| `elevenlabs` | `POST {base}/speech-to-text` and `POST {base}/text-to-speech/{voice_id}` | `AUDIO_*_API_KEY`, else `ELEVENLABS_API_KEY`; sent as `xi-api-key` | `https://api.elevenlabs.io/v1` | Required (voice ID) | mp3, wav, opus | Speed yes, instructions no | All accepted types |
| `openai_chat_audio` | `POST {base}/chat/completions` with an audio model | `AUDIO_*_API_KEY`, optional, as a Bearer token | Required | Required | mp3, wav, opus, flac | Instructions yes, speed no | WAV and MP3 only |

The checks described under [Access control](#access-control-and-data-classification),
[Limits and timeouts](#limits-and-timeouts) and
[Privacy, logging and retention](#privacy-logging-and-retention) are applied
in the Atlas routes and in one shared HTTP layer, so they hold for every
provider. Hosted providers are contacted only when an administrator selects
them; an installation on the default provider talking to a self-hosted server
contacts nothing outside it.

Validate model and voice identifiers against the provider's current
documentation. The names in the examples below are the ones in use when this
page was written; Atlas passes whatever you configure through unchanged.

### litellm

Atlas calls the LiteLLM library in process; there is no LiteLLM proxy server
(for that, use the [gateway example](#example-through-a-litellm-proxy-or-another-gateway)
with the default provider). The model prefix chooses the upstream service
(`openai/`, `elevenlabs/`, `groq/`, `deepgram/`, `mistral/`, `vertex_ai/` and
others LiteLLM supports for audio). The base URL is optional and only
overrides the provider's own endpoint. The API key is optional: when
`AUDIO_*_API_KEY` is empty LiteLLM reads the provider's usual variable, so
those variables must be present in the Atlas process environment.

Groq for dictation and ElevenLabs for read-aloud:

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_PROVIDER=litellm
AUDIO_STT_MODEL=groq/whisper-large-v3
AUDIO_STT_API_KEY=gsk-your-groq-key

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_PROVIDER=litellm
AUDIO_TTS_MODEL=elevenlabs/eleven_multilingual_v2
AUDIO_TTS_VOICE=your-elevenlabs-voice-id
AUDIO_TTS_API_KEY=your-elevenlabs-key
```

OpenAI through LiteLLM:

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_PROVIDER=litellm
AUDIO_STT_MODEL=openai/whisper-1
AUDIO_STT_API_KEY=sk-your-openai-key

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_PROVIDER=litellm
AUDIO_TTS_MODEL=openai/tts-1
AUDIO_TTS_VOICE=alloy
AUDIO_TTS_API_KEY=sk-your-openai-key
```

Other combinations that work for dictation are `deepgram/nova-3`,
`mistral/...` models and `elevenlabs/scribe_v1`. Mistral text-to-speech is
not supported: Mistral returns JSON with base64 audio rather than audio, and
Atlas refuses a reply that is not audio.

How this adapter differs from the others: LiteLLM's retries are off so the
configured timeout stays a real ceiling; LiteLLM's logging callbacks are
switched off for these calls (`no-log`); failures are reported by category and
status only, never by LiteLLM's message text; LiteLLM buffers a reply, so the
`AUDIO_TTS_MAX_AUDIO_BYTES` cap is checked after the reply arrives rather than
while it is read; and redirect handling and the HTTP client are LiteLLM's own,
not Atlas's.

### gemini

Gemini has no OpenAI-style audio routes, so Atlas uses `generateContent` with
the key in the `x-goog-api-key` header. The key comes from `AUDIO_*_API_KEY`,
else `GOOGLE_API_KEY`, else `GEMINI_API_KEY`, so an installation that already
sets one of those for its chat models needs no extra variable. The bundled
`docker-compose.yml` passes all three into the container.

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_PROVIDER=gemini
AUDIO_STT_MODEL=gemini-2.5-flash
AUDIO_STT_MAX_AUDIO_BYTES=14680064

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_PROVIDER=gemini
AUDIO_TTS_MODEL=gemini-2.5-flash-preview-tts
AUDIO_TTS_VOICE=Kore
AUDIO_TTS_INSTRUCTIONS=Say in a calm, clear voice

# One key for both, unless GOOGLE_API_KEY or GEMINI_API_KEY is already set:
GOOGLE_API_KEY=your-gemini-key
```

- **Dictation** sends the clip inline (base64) with a fixed prompt that asks
  for a verbatim transcript. Gemini limits the size of an inline request
  (about 20 MB in total), and base64 makes the clip about a third larger, so
  lower `AUDIO_STT_MAX_AUDIO_BYTES` to about 14 MB as above. Chrome and
  Firefox record WebM/Opus and Safari records MP4; Atlas declares an MP4
  recording to Gemini as `audio/m4a`, the name on Gemini's list of supported
  audio types, and sends the other types under their usual names.
- **Read-aloud** receives raw PCM audio, which Atlas wraps as WAV. The output
  is always WAV and `AUDIO_TTS_RESPONSE_FORMAT` is ignored. The voice is
  optional and is a prebuilt voice name such as `Kore`. Instructions are
  placed in front of the text as `<instructions>: <text>`. Speed is not
  supported.

### elevenlabs

The native ElevenLabs API. The key goes in the `xi-api-key` header and comes
from `AUDIO_*_API_KEY`, else `ELEVENLABS_API_KEY`.

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_PROVIDER=elevenlabs
AUDIO_STT_MODEL=scribe_v2

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_PROVIDER=elevenlabs
AUDIO_TTS_MODEL=eleven_multilingual_v2
AUDIO_TTS_VOICE=your-elevenlabs-voice-id
AUDIO_TTS_RESPONSE_FORMAT=mp3

# One key for both:
ELEVENLABS_API_KEY=your-elevenlabs-key
```

- The voice is required and is a voice ID, not a voice name.
- Only `mp3`, `wav` and `opus` are accepted for the response format. `wav` is
  requested from ElevenLabs as raw PCM and framed as WAV by Atlas.
- `AUDIO_TTS_SPEED` is sent as `voice_settings.speed`. ElevenLabs accepts a
  narrower range than Atlas does (roughly 0.7 to 1.2) and rejects a value
  outside it with an error, so keep to that range with this provider.
- `AUDIO_TTS_INSTRUCTIONS` is not sent.

ElevenLabs is also reachable through the `litellm` provider
(`elevenlabs/scribe_v1`, `elevenlabs/eleven_multilingual_v2`) as an
alternative to this adapter.

### openai_chat_audio

For audio-capable chat models such as `gpt-audio-1.5`, which are served on
`/chat/completions` rather than on the audio routes. The base URL is required;
the Bearer key is optional.

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_PROVIDER=openai_chat_audio
AUDIO_STT_BASE_URL=https://api.openai.com/v1
AUDIO_STT_MODEL=gpt-audio-1.5
AUDIO_STT_API_KEY=sk-your-openai-key

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_PROVIDER=openai_chat_audio
AUDIO_TTS_BASE_URL=https://api.openai.com/v1
AUDIO_TTS_MODEL=gpt-audio-1.5
AUDIO_TTS_VOICE=alloy
AUDIO_TTS_RESPONSE_FORMAT=mp3
AUDIO_TTS_API_KEY=sk-your-openai-key
```

- Read-aloud requests text and audio output with the configured voice and
  format (`mp3`, `wav`, `opus` or `flac`). The voice is required.
  `AUDIO_TTS_INSTRUCTIONS` is appended to the system message; speed is not
  supported. Atlas checks that the decoded audio starts with the signature of
  the requested format.
- Dictation accepts **WAV and MP3 uploads only**, which is all the chat
  audio input takes. Browsers record WebM, Ogg or MP4, never WAV or MP3, so
  with this provider the microphone button and its Settings switch are not
  shown: `/api/config/shell` lists the accepted upload types
  (`stt.accepted_media_types`) and the browser draws the button only when it
  can record one of them. `/api/audio/transcriptions` still serves API
  clients that upload WAV or MP3, and refuses other types with 415 ("The
  configured speech service does not accept this audio format."). For
  browser dictation choose another provider.
- A chat model follows instructions rather than a fixed contract. Atlas asks
  it to read the text verbatim or transcribe verbatim, but it can occasionally
  add or change words. Prefer a dedicated speech model where exactness matters.

## Button visibility: administrator default and user preference

Enabling a capability and showing its button are separate decisions:

1. `FEATURE_AUDIO_*_ENABLED` (with complete configuration, and the user's
   group membership if `AUDIO_*_GROUPS` is set) decides whether the
   capability **exists** for a user. If it does not, there is no button, no
   switch in Settings, and the endpoint refuses the request.
2. `AUDIO_*_SHOW_BUTTON_BY_DEFAULT` is the starting position of the button
   for users who have not chosen. Set it to `false` to make a capability
   opt-in: available, but hidden until a user turns it on.
3. Each user can show or hide each button under **Settings > General >
   Voice**. Their choice is stored with their other interface preferences in
   the browser (`chatui-settings` in local storage) and overrides the
   default in either direction. **Reset to defaults** returns both switches
   to following the administrator default.

A user preference can never turn on a capability that step 1 denies.

The browser learns what to draw from `/api/config/shell` and `/api/config`:

```json
"audio": {
  "stt": {"enabled": true, "show_button_by_default": true,
          "accepted_media_types": ["audio/flac", "audio/mp4", "audio/mpeg",
                                   "audio/ogg", "audio/wav", "audio/webm"],
          "max_audio_bytes": 26214400, "max_duration_seconds": 300,
          "allowed_data_classifications": null},
  "tts": {"enabled": false, "show_button_by_default": true}
}
```

`enabled` is already specific to the signed-in user. `accepted_media_types`
lists the upload types the configured provider transcribes; the browser hides
the microphone when it cannot record any of them (see
[openai_chat_audio](#openai_chat_audio)). The response never
contains the provider, a base URL, model, voice, delivery instructions or
credential.

## Example: self-hosted Speaches

This and the next example use the default `openai_compatible` provider.

[Speaches](https://speaches.ai/) is a self-hosted server that provides both
routes. One instance can serve both capabilities.

1. Start Speaches. Its documentation publishes
   `ghcr.io/speaches-ai/speaches:latest-cpu` and `:latest-cuda`, listening on
   port 8000:

   ```bash
   podman run --detach --name speaches --publish 8000:8000 \
     ghcr.io/speaches-ai/speaches:latest-cpu
   ```

   See the Speaches installation guide for GPU flags and for mounting a
   volume so downloaded models survive a container restart.

2. Download the models you will use. Speaches does not download a model on
   first request:

   ```bash
   export SPEACHES_BASE_URL=http://localhost:8000
   curl -X POST "$SPEACHES_BASE_URL/v1/models/Systran/faster-whisper-large-v3"
   curl -X POST "$SPEACHES_BASE_URL/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX"
   curl "$SPEACHES_BASE_URL/v1/models"        # what is installed
   ```

3. Confirm both routes answer before involving Atlas:

   ```bash
   curl "$SPEACHES_BASE_URL/v1/audio/transcriptions" \
     -F "file=@sample.wav" -F "model=Systran/faster-whisper-large-v3"
   curl "$SPEACHES_BASE_URL/v1/audio/speech" -H "Content-Type: application/json" \
     -d '{"model": "speaches-ai/Kokoro-82M-v1.0-ONNX", "voice": "af_heart", "input": "Hello."}' \
     --output hello.mp3
   ```

4. Configure Atlas:

   ```bash
   FEATURE_AUDIO_STT_ENABLED=true
   AUDIO_STT_BASE_URL=http://speaches:8000/v1
   AUDIO_STT_MODEL=Systran/faster-whisper-large-v3

   FEATURE_AUDIO_TTS_ENABLED=true
   AUDIO_TTS_BASE_URL=http://speaches:8000/v1
   AUDIO_TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
   AUDIO_TTS_VOICE=af_heart
   AUDIO_TTS_RESPONSE_FORMAT=mp3
   ```

Notes:

- **Validate the identifiers against your server.** The model and voice
  names above are the ones the Speaches documentation used when this page
  was written. Atlas passes whatever you configure through unchanged, so a
  name the server does not know surfaces to users as "The speech service
  does not offer the configured endpoint or model." Use step 3 with your
  exact values; `GET /v1/models` lists what is installed.
- Speaches documents `opus` and `aac` as unsupported for
  `response_format`; use `mp3` or `wav`.
- If you set an API key on Speaches (its `API_KEY` setting), put the same
  value in `AUDIO_STT_API_KEY` and `AUDIO_TTS_API_KEY`.
- Atlas must be able to reach the server from every backend replica. Nothing
  is contacted until a user presses a button, and nothing outside the
  configured base URL is ever contacted.

## Example: through a LiteLLM proxy or another gateway

A gateway needs no Atlas code change: point the base URL at the gateway and
use the gateway's model names and key. For a LiteLLM proxy, declare the audio
models in its `config.yaml`:

```yaml
model_list:
  - model_name: whisper
    litellm_params:
      model: openai/Systran/faster-whisper-large-v3
      api_base: http://speaches:8000/v1
      api_key: "none"
    model_info:
      mode: audio_transcription
  - model_name: tts
    litellm_params:
      model: openai/speaches-ai/Kokoro-82M-v1.0-ONNX
      api_base: http://speaches:8000/v1
      api_key: "none"
    model_info:
      mode: audio_speech
```

Then in Atlas:

```bash
FEATURE_AUDIO_STT_ENABLED=true
AUDIO_STT_BASE_URL=http://litellm:4000/v1
AUDIO_STT_MODEL=whisper
AUDIO_STT_API_KEY=sk-your-litellm-virtual-key

FEATURE_AUDIO_TTS_ENABLED=true
AUDIO_TTS_BASE_URL=http://litellm:4000/v1
AUDIO_TTS_MODEL=tts
AUDIO_TTS_VOICE=af_heart
AUDIO_TTS_API_KEY=sk-your-litellm-virtual-key
```

Check the proxy configuration against the LiteLLM documentation for the
version you run. A hosted OpenAI-compatible API is configured the same way:
its base URL, its model and voice names, and a key.

Using a gateway for one capability and a direct server for the other is
fine; the two sets of variables do not interact.

## Access control and data classification

- **Authentication.** Both endpoints are ordinary `/api` routes behind the
  Atlas authentication middleware and the global rate limit. There is no
  unauthenticated path to the audio server.
- **Groups.** `AUDIO_STT_GROUPS` / `AUDIO_TTS_GROUPS` restrict a capability
  to members of at least one listed group, using the same group lookup as
  model and MCP server `groups`. A user outside the groups sees no button
  and gets 404, the same as if the capability were off.
- **Compliance levels.** Dictated speech and the text of a reply are
  conversation data leaving Atlas for another service. When
  `FEATURE_COMPLIANCE_LEVELS_ENABLED=true`, each request carries the
  conversation's active level and is refused (403) unless that level is
  listed in the capability's `AUDIO_*_ALLOWED_DATA_CLASSIFICATIONS`. A hosted
  provider is an external service: approve it only for the levels your policy
  allows to leave your environment. This is
  the rule models, MCP servers and RAG sources follow: an endpoint that
  declares nothing is approved for no classified conversation, and
  `allowed_with` does not widen it. With
  `FEATURE_COMPLIANCE_LEVEL_REQUIRED=true` a request that names no level is
  refused too. The buttons are hidden in a conversation whose level the
  endpoint is not approved for.

  The level is the one the browser states for the conversation it is showing,
  as it does for a chat turn; these endpoints receive text or audio, not a
  conversation id, so Atlas does not look the conversation up. The gate keeps
  the Atlas UI from sending a classified conversation's content to an
  endpoint that is not approved for it. It does not stop a user from
  deliberately mislabeling content they could equally type into any other
  conversation, so approve an audio endpoint with the same care as a model.

  ```bash
  AUDIO_STT_ALLOWED_DATA_CLASSIFICATIONS=Public,Internal
  AUDIO_TTS_ALLOWED_DATA_CLASSIFICATIONS=Public
  ```

## Limits and timeouts

| Limit | Where it is enforced |
| ----- | -------------------- |
| Upload size (`AUDIO_STT_MAX_AUDIO_BYTES`) | Server, while the body is read: an oversized upload is refused with 413 without being buffered. The browser also refuses to upload an over-limit recording |
| Recording length (`AUDIO_STT_MAX_DURATION_SECONDS`) | The microphone button stops at the limit and transcribes what it has. The server also refuses a request whose reported duration is more than 5 seconds over the limit (the allowance covers a stop timer the browser ran late). Atlas does not decode audio, so for clients other than the Atlas UI the byte limit is the hard bound |
| Upload type | Server: the declared type must be WebM, Ogg, MP4/M4A, MP3, WAV or FLAC **and** the file must begin with that container's signature, otherwise 415. A provider that takes fewer types (`openai_chat_audio` takes WAV and MP3 only) refuses the others with 415 before anything is sent |
| Text length (`AUDIO_TTS_MAX_INPUT_CHARS`) | Server, 413. The read-aloud button trims a longer message at a sentence boundary and tells the user it did |
| Reply size (`AUDIO_TTS_MAX_AUDIO_BYTES`) | Server: a larger reply from the audio server is discarded and the request fails with 502. Direct adapters stop reading at the limit; with `litellm` the size is checked after LiteLLM has returned the reply |
| Time (`AUDIO_*_TIMEOUT_SECONDS`) | Server: a ceiling on the whole exchange with the audio server, 504 when exceeded. The same value bounds how long the browser may take to send the request body (408), so a slow upload cannot hold a relay slot |
| Concurrency (`AUDIO_MAX_CONCURRENT_REQUESTS`) | Server, per backend process: a request beyond the limit is refused with 503 before its body is read |

**Memory.** A clip is held in memory while it is relayed, briefly twice (the
upload and the copy being sent on), and a synthesized reply once. Budget
roughly `AUDIO_MAX_CONCURRENT_REQUESTS` x 2 x `AUDIO_STT_MAX_AUDIO_BYTES` per
backend process in the worst case (about 400 MB with the defaults), and lower
either setting if replicas are small. Providers that return audio inside JSON
(`gemini` and `openai_chat_audio`) send it base64 encoded, about a third
larger, and Atlas holds that reply and then the decoded audio, so a
synthesized reply can briefly cost about twice `AUDIO_TTS_MAX_AUDIO_BYTES`
(more for `gemini`, where the WAV frame is built too). `litellm` holds the
whole reply in memory before the size check. The concurrency limit is counted in
each process separately: it protects that process's memory and is not a
deployment-wide quota.

Redirects from the audio server are not followed by the direct adapters
(`openai_compatible`, `gemini`, `elevenlabs`, `openai_chat_audio`), so audio
and credentials only ever go to the configured base URL. The `litellm`
provider uses LiteLLM's own HTTP client and its redirect behavior.

## Privacy, logging and retention

- **Atlas keeps no audio.** A recording exists in the browser tab's memory
  until it is uploaded, and in the backend's memory for the duration of the
  request. An upload larger than about 1 MB is spooled by the web framework
  to a temporary file while it is being read; that file is deleted before
  the request returns, whether it succeeded or failed. Synthesized speech is
  relayed to the browser with `Cache-Control: no-store`, held as an
  in-memory object URL while the message is playing, and released on stop,
  at the end of playback, or when the message leaves the page. Reading the
  same message again makes a new request.
- **Atlas keeps no transcript.** The transcript is returned to the browser
  and placed in the message box. It becomes part of a conversation only if
  the user sends it, at which point the normal chat-history settings apply.
- **Nothing is logged but metadata.** With
  `FEATURE_METRICS_LOGGING_ENABLED=true`, each request writes one
  `[METRIC]` line (`audio_transcription` or `audio_speech`) with the user,
  the provider name (`provider=gemini`, ...), byte and character counts, the
  media type and the elapsed time. Failures log the provider name, the error
  category and the upstream status code, so an alert can be scoped to one
  provider. The audio, the
  transcript, the text to speak, the uploaded filename, the audio server's
  error body and the API keys are never logged.
- **The audio server has its own retention.** What it logs or stores is
  governed by that server's configuration, not by Atlas. Review it as you
  would any other service that receives conversation data. For a hosted
  provider (Google, ElevenLabs, OpenAI, Groq and so on) that means the
  provider's terms and retention, and your data classification policy applies
  to it as to any external model.
- **Provider error text is dropped.** Every provider's error body is
  discarded: the direct adapters never read it, and the `litellm` adapter
  reports only the failure category and upstream status, not LiteLLM's
  message. LiteLLM's logging callbacks are switched off for these calls.
- **The browser is told nothing about the provider.** `/api/config/shell`
  never reveals the provider, URL, model, voice, instructions or credential.

## Browser requirements

- Recording uses the browser's own `MediaRecorder`; no third-party speech
  service or script is involved. The microphone is only available on a
  secure origin (HTTPS, or `localhost` during development). On plain HTTP
  the button explains that HTTPS is required.
- The first use prompts for microphone permission. If it is denied, the
  button explains how to allow it in the browser's site settings.
- The recorder uses the first format the browser supports out of WebM/Opus,
  Ogg/Opus and MP4. A browser that supports none of them gets a message
  saying so rather than a failed upload.
- Some browsers (notably Safari on iOS) only start sound in direct response
  to a tap. If the wait for the audio outlives the tap, the read-aloud button
  keeps the generated audio and shows **Play**; a second tap starts it
  without generating it again. This path is implemented from the documented
  browser behavior and has not been verified on an iOS device.
- If a reverse proxy in front of Atlas sets a `Permissions-Policy` header,
  it must allow `microphone=(self)`.
- A proxy with a request body size limit must allow uploads of
  `AUDIO_STT_MAX_AUDIO_BYTES`.

## Content Security Policy

Read-aloud plays audio from a `blob:` URL. While
`FEATURE_AUDIO_TTS_ENABLED=true`, Atlas appends `media-src 'self' blob:` to
the `Content-Security-Policy` header **if the policy has no `media-src`
directive**. If your `SECURITY_CSP_VALUE` (or a proxy that sets the header)
defines `media-src` itself, it is left as written and must include `blob:`.
With text-to-speech off the header is unchanged.

## Errors

| Status | Meaning |
| ------ | ------- |
| 404 | The capability is off, incompletely configured, or not available to this user |
| 403 | The audio endpoint is not approved for the conversation's security level |
| 408 | The request body took longer than `AUDIO_*_TIMEOUT_SECONDS` to arrive |
| 413 | Recording too large or too long, or text too long |
| 415 | The upload is not an accepted audio type, or is not what it claims to be, or the configured provider does not accept this format ("The configured speech service does not accept this audio format.") |
| 422 | Missing file or text, or the audio server could not process the request |
| 502 | The audio server is unreachable, rejected the credential, does not know the model, or returned something that is not a transcript or audio |
| 503 | The audio server is rate limiting, or this Atlas process is already relaying `AUDIO_MAX_CONCURRENT_REQUESTS` audio requests |
| 504 | The audio server did not answer within the timeout |

Users see a short message for each. The audio server's own error text is
not forwarded, because it can repeat the content that was sent.

## Trying it without a speech model

`mocks/openai-audio-mock/` implements both routes with canned replies; see
its [README](../../mocks/openai-audio-mock/README.md).
`bash test/run_pr_validation.sh 1251` starts it together with a backend and
exercises the endpoints end to end.
