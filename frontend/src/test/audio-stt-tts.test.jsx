/**
 * Dictation and read-aloud (issue #1251).
 *
 * jsdom has no microphone, MediaRecorder or audio playback, so the browser
 * APIs are replaced with small fakes that record what the components ask of
 * them. What is asserted is the contract the issue sets out:
 *
 * - a capability the administrator has not made available cannot be shown by
 *   a user preference, and has no Settings switch
 * - with no preference saved the administrator's default decides; a saved
 *   preference overrides it in either direction
 * - dictation records, stops or cancels, transcribes, and hands the text
 *   back without sending anything; permission denial and an unsupported
 *   recording format produce a message the user can act on
 * - read-aloud generates audio once, then plays, pauses, stops and changes
 *   speed locally
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor } from '@testing-library/react'

import { resolveAudioControl, audioButtonPreference, audioCapabilityOffered, recordableMimeTypes } from '../utils/audioControls'
import { toSpeechText, fitSpeechText } from '../utils/speechText'
import { describeMicrophoneError, pickRecordingMimeType } from '../hooks/useDictation'
import AudioSettingsSection from '../components/AudioSettingsSection'
import DictationButton from '../components/DictationButton'
import ReadAloudButton from '../components/ReadAloudButton'
import { ToastContext } from '../components/ui/toastContext'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const ON = { enabled: true, show_button_by_default: true }
const ON_HIDDEN = { enabled: true, show_button_by_default: false }
const OFF = { enabled: false, show_button_by_default: true }

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), dismiss: vi.fn() }
const withToast = ui => <ToastContext.Provider value={toast}>{ui}</ToastContext.Provider>

const chatState = (overrides = {}) => ({
  audio: { stt: ON, tts: { ...ON, max_input_chars: 4096 } },
  settings: {},
  updateSettings: vi.fn(),
  complianceLevels: [],
  activeComplianceFilter: null,
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  useChat.mockReturnValue(chatState())
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('button visibility rule', () => {
  it('never shows a capability that is not available, whatever the preference', () => {
    expect(resolveAudioControl(OFF, true)).toEqual({ available: false, visible: false })
    expect(resolveAudioControl(undefined, true)).toEqual({ available: false, visible: false })
  })

  it('follows the administrator default when the user has not chosen', () => {
    for (const unset of [null, undefined]) {
      expect(resolveAudioControl(ON, unset).visible).toBe(true)
      expect(resolveAudioControl(ON_HIDDEN, unset).visible).toBe(false)
    }
    expect(resolveAudioControl(ON_HIDDEN, null).available).toBe(true)
  })

  it('lets an explicit preference override the default in both directions', () => {
    expect(resolveAudioControl(ON, false).visible).toBe(false)
    expect(resolveAudioControl(ON_HIDDEN, true).visible).toBe(true)
    expect(audioButtonPreference(ON_HIDDEN, true)).toBe(true)
    expect(audioButtonPreference(ON, false)).toBe(false)
  })

  it('hides a capability the conversation level is not approved for', () => {
    const levels = [{ name: 'Public' }, { name: 'Internal', aliases: ['INT'] }]
    const approved = { ...ON, allowed_data_classifications: ['Internal'] }
    expect(resolveAudioControl(approved, null, { levels, activeLevel: 'INT' }).visible).toBe(true)
    expect(resolveAudioControl(approved, true, { levels, activeLevel: 'Public' })).toEqual({ available: false, visible: false })
    // Declares nothing: approved for no classified conversation.
    expect(resolveAudioControl({ ...ON, allowed_data_classifications: [] }, true, { levels, activeLevel: 'Public' }).visible).toBe(false)
    // No active level, or compliance off (null): nothing to check.
    expect(resolveAudioControl(approved, null, { levels, activeLevel: null }).visible).toBe(true)
    expect(resolveAudioControl({ ...ON, allowed_data_classifications: null }, null, { levels, activeLevel: 'Public' }).visible).toBe(true)
    // Level definitions not loaded yet: do not flash the button away.
    expect(resolveAudioControl(approved, null, { levels: [], activeLevel: 'Public' }).visible).toBe(true)
  })
})

describe('accepted upload types (issue #1255)', () => {
  const WAV_MP3_ONLY = { ...ON, accepted_media_types: ['audio/mpeg', 'audio/wav'] }
  const ALL_TYPES = { ...ON, accepted_media_types: ['audio/flac', 'audio/mp4', 'audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/webm'] }

  it('hides dictation when the service takes nothing a browser can record', () => {
    expect(audioCapabilityOffered(WAV_MP3_ONLY)).toBe(false)
    expect(resolveAudioControl(WAV_MP3_ONLY, true)).toEqual({ available: false, visible: false })
  })

  it('keeps dictation when the service takes a recorder format, or does not say', () => {
    expect(resolveAudioControl(ALL_TYPES, null).visible).toBe(true)
    expect(resolveAudioControl({ ...ON, accepted_media_types: ['audio/mp4'] }, null).visible).toBe(true)
    expect(resolveAudioControl(ON, null).visible).toBe(true)
  })

  it('only offers recorder types the service accepts', () => {
    expect(recordableMimeTypes(['audio/mp4', 'audio/wav'])).toEqual(['audio/mp4'])
    expect(recordableMimeTypes(['audio/webm'])).toEqual(['audio/webm;codecs=opus', 'audio/webm'])
    expect(recordableMimeTypes(undefined)).toHaveLength(4)
  })

  it('has no Settings switch for dictation the browser cannot use', () => {
    useChat.mockReturnValue(chatState({ audio: { stt: WAV_MP3_ONLY, tts: ON } }))
    render(<AudioSettingsSection />)
    expect(screen.queryByRole('switch', { name: 'Show microphone button' })).toBeNull()
    expect(screen.getByRole('switch', { name: 'Show read-aloud button' })).toBeTruthy()
  })
})

describe('Settings switches', () => {
  it('offers no switch for a capability that is not available', () => {
    useChat.mockReturnValue(chatState({ audio: { stt: OFF, tts: ON } }))
    render(<AudioSettingsSection />)
    expect(screen.queryByRole('switch', { name: 'Show microphone button' })).toBeNull()
    expect(screen.getByRole('switch', { name: 'Show read-aloud button' })).toBeTruthy()
  })

  it('renders nothing when neither capability is available', () => {
    useChat.mockReturnValue(chatState({ audio: { stt: OFF, tts: OFF } }))
    const { container } = render(<AudioSettingsSection />)
    expect(container.innerHTML).toBe('')
  })

  it('shows the administrator default until the user chooses, then saves the choice', () => {
    const updateSettings = vi.fn()
    useChat.mockReturnValue(chatState({ audio: { stt: ON_HIDDEN, tts: ON }, updateSettings }))
    render(<AudioSettingsSection />)
    const mic = screen.getByRole('switch', { name: 'Show microphone button' })
    const speaker = screen.getByRole('switch', { name: 'Show read-aloud button' })
    expect(mic.getAttribute('aria-checked')).toBe('false')
    expect(speaker.getAttribute('aria-checked')).toBe('true')
    expect(screen.getAllByText(/following your administrator's default/)).toHaveLength(2)

    fireEvent.click(mic)
    expect(updateSettings).toHaveBeenCalledWith({ showSttButton: true })
    fireEvent.click(speaker)
    expect(updateSettings).toHaveBeenCalledWith({ showTtsButton: false })
  })

  it('reflects a saved preference over the default', () => {
    useChat.mockReturnValue(chatState({
      audio: { stt: ON_HIDDEN, tts: ON },
      settings: { showSttButton: true, showTtsButton: false },
    }))
    render(<AudioSettingsSection />)
    expect(screen.getByRole('switch', { name: 'Show microphone button' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: 'Show read-aloud button' }).getAttribute('aria-checked')).toBe('false')
    expect(screen.queryByText(/following your administrator's default/)).toBeNull()
  })
})

describe('speech text', () => {
  it('drops markup and replaces code blocks', () => {
    const spoken = toSpeechText('## Title\n\nSome **bold** and `code` with a [link](http://x.test).\n\n```js\nconst a = 1\n```\n\n- item one\n- item two')
    expect(spoken).not.toMatch(/[#*`\]]|http|const a/)
    expect(spoken).toContain('Some bold and code with a link.')
    expect(spoken).toContain('(code block omitted)')
    expect(spoken).toContain('item one')
    expect(toSpeechText(null)).toBe('')
  })

  it('keeps what the author marked as inline code, and comparisons in prose', () => {
    // Inside backticks nothing is markup: not a tag, not a link.
    expect(toSpeechText('Use `Array<String>` here.')).toBe('Use Array<String> here.')
    expect(toSpeechText('Index with `a[1](2)` first.')).toBe('Index with a[1](2) first.')
    expect(toSpeechText('Keep `**stars**` and `_under_` literal.')).toBe('Keep **stars** and _under_ literal.')
    // A bare "<" in prose is a comparison, not the start of a tag.
    expect(toSpeechText('Run it if a < b > c holds.')).toBe('Run it if a < b > c holds.')
    expect(toSpeechText('When x<5 and y>2.')).toBe('When x<5 and y>2.')
    // Real tags still go.
    expect(toSpeechText('Line one<br>line two <span class="x">kept</span>.')).toBe('Line one line two kept .')
  })

  it('never cuts through the middle of an emoji', () => {
    const rocket = '\u{1F680}'
    const text = `${'a'.repeat(9)}${rocket}${'b'.repeat(20)}`
    // The limit lands between the two halves of the surrogate pair.
    const fitted = fitSpeechText(text, 10)
    expect(fitted).toEqual({ text: 'a'.repeat(9), truncated: true })
    expect(/[\uD800-\uDBFF]$/.test(fitted.text)).toBe(false)
    // A limit past the pair keeps it whole.
    expect(fitSpeechText(text, 11).text).toBe(`${'a'.repeat(9)}${rocket}`)
  })

  it('cuts over-long text at a sentence end and says so', () => {
    const text = 'First sentence here. Second sentence here. Third sentence is long.'
    expect(fitSpeechText(text, 1000)).toEqual({ text, truncated: false })
    expect(fitSpeechText(text, 45)).toEqual({ text: 'First sentence here. Second sentence here.', truncated: true })
    expect(fitSpeechText('x'.repeat(100), 10)).toEqual({ text: 'x'.repeat(10), truncated: true })
  })
})

// --- dictation -------------------------------------------------------------

class FakeRecorder {
  static supported = ['audio/webm;codecs=opus']
  static isTypeSupported = type => FakeRecorder.supported.includes(type)
  static instances = []

  constructor(stream, options) {
    this.stream = stream
    this.mimeType = options.mimeType
    this.state = 'inactive'
    FakeRecorder.instances.push(this)
  }

  start() { this.state = 'recording' }

  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio-bytes'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

const installMicrophone = ({ getUserMedia } = {}) => {
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] }
  FakeRecorder.instances = []
  FakeRecorder.supported = ['audio/webm;codecs=opus']
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  window.MediaRecorder = FakeRecorder
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: getUserMedia || vi.fn().mockResolvedValue(stream) },
  })
  return { track }
}

const jsonResponse = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

describe('dictation button', () => {
  const renderButton = (onTranscript = vi.fn()) => {
    render(withToast(<DictationButton onTranscript={onTranscript} maxDurationSeconds={60} maxAudioBytes={1000} complianceLevel="Internal" />))
    return onTranscript
  }

  it('records, stops, transcribes and hands the text back without sending', async () => {
    const { track } = installMicrophone()
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { text: '  hello from the microphone ' }))
    vi.stubGlobal('fetch', fetchMock)
    const onTranscript = renderButton()

    fireEvent.click(screen.getByTestId('dictation-start'))
    await screen.findByTestId('dictation-stop')
    expect(screen.getByTestId('dictation-status').textContent).toBe('Recording')

    fireEvent.click(screen.getByTestId('dictation-stop'))
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('hello from the microphone'))
    await screen.findByTestId('dictation-start')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/audio/transcriptions')
    expect(init.method).toBe('POST')
    expect(init.body.get('file').type).toBe('audio/webm;codecs=opus')
    expect(init.body.get('compliance_level')).toBe('Internal')
    expect(init.body.get('duration_seconds')).toMatch(/^\d+\.\d$/)
    // The microphone is released as soon as recording ends.
    expect(track.stop).toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('cancel discards the recording: nothing is uploaded or inserted', async () => {
    const { track } = installMicrophone()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const onTranscript = renderButton()

    fireEvent.click(screen.getByTestId('dictation-start'))
    await screen.findByTestId('dictation-stop')
    fireEvent.click(screen.getByTestId('dictation-cancel'))

    await screen.findByTestId('dictation-start')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onTranscript).not.toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalled()
  })

  it('explains a denied microphone permission', async () => {
    const denied = Object.assign(new Error('denied'), { name: 'NotAllowedError' })
    installMicrophone({ getUserMedia: vi.fn().mockRejectedValue(denied) })
    renderButton()
    fireEvent.click(screen.getByTestId('dictation-start'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Microphone access is blocked.*site settings/)))
    expect(screen.getByTestId('dictation-start')).toBeTruthy()
  })

  it('explains an unsupported recording format without asking for the microphone', async () => {
    const getUserMedia = vi.fn()
    installMicrophone({ getUserMedia })
    FakeRecorder.supported = []
    renderButton()
    expect(pickRecordingMimeType()).toBeNull()
    fireEvent.click(screen.getByTestId('dictation-start'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/cannot record audio in a format/)))
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it('falls back to a format the browser can record', () => {
    installMicrophone()
    FakeRecorder.supported = ['audio/mp4']
    expect(pickRecordingMimeType()).toBe('audio/mp4')
  })

  it('picks only a format the service accepts', () => {
    installMicrophone()
    FakeRecorder.supported = ['audio/webm;codecs=opus', 'audio/mp4']
    expect(pickRecordingMimeType(['audio/mp4'])).toBe('audio/mp4')
    expect(pickRecordingMimeType(['audio/wav'])).toBeNull()
    expect(pickRecordingMimeType(undefined)).toBe('audio/webm;codecs=opus')
  })

  it('records in the accepted format it was given', async () => {
    installMicrophone()
    FakeRecorder.supported = ['audio/webm;codecs=opus', 'audio/mp4']
    const getUserMedia = navigator.mediaDevices.getUserMedia
    render(withToast(<DictationButton onTranscript={vi.fn()} maxDurationSeconds={60} maxAudioBytes={1000} acceptedMediaTypes={['audio/mp4']} />))
    fireEvent.click(screen.getByTestId('dictation-start'))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled())
    await screen.findByTestId('dictation-stop')
    expect(FakeRecorder.instances.at(-1).mimeType).toBe('audio/mp4')
  })

  it('shows the server error and returns to idle', async () => {
    installMicrophone()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(504, { detail: 'The speech service did not answer in time.' })))
    const onTranscript = renderButton()
    fireEvent.click(screen.getByTestId('dictation-start'))
    fireEvent.click(await screen.findByTestId('dictation-stop'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The speech service did not answer in time.'))
    expect(onTranscript).not.toHaveBeenCalled()
    await screen.findByTestId('dictation-start')
  })

  it('refuses a recording over the size limit before uploading', async () => {
    installMicrophone()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(withToast(<DictationButton onTranscript={vi.fn()} maxDurationSeconds={60} maxAudioBytes={4} />))
    fireEvent.click(screen.getByTestId('dictation-start'))
    fireEvent.click(await screen.findByTestId('dictation-stop'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/too large/)))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('stops by itself at the duration limit and transcribes what it has', async () => {
    installMicrophone()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { text: 'up to the limit' })))
    const onTranscript = vi.fn()
    render(withToast(<DictationButton onTranscript={onTranscript} maxDurationSeconds={0.05} maxAudioBytes={1000} />))
    fireEvent.click(screen.getByTestId('dictation-start'))
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('up to the limit'))
    expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/Recording stopped at the .*limit/))
  })

  it.each([[undefined], [null], [NaN], [0], ['soon']])('keeps recording when the duration limit is %s', async (limit) => {
    installMicrophone()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { text: 'still here' })))
    const onTranscript = vi.fn()
    render(withToast(<DictationButton onTranscript={onTranscript} maxDurationSeconds={limit} maxAudioBytes={1000} />))
    fireEvent.click(screen.getByTestId('dictation-start'))
    await screen.findByTestId('dictation-stop')
    // An unusable limit must not become a zero-length recording.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)) })
    expect(screen.getByTestId('dictation-stop')).toBeTruthy()
    expect(toast.info).not.toHaveBeenCalled()
    expect(onTranscript).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('dictation-stop'))
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('still here'))
  })

  it('releases the microphone when cancelled during the permission prompt', async () => {
    const track = { stop: vi.fn() }
    let grant
    installMicrophone({ getUserMedia: vi.fn(() => new Promise(resolve => { grant = resolve })) })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderButton()
    fireEvent.click(screen.getByTestId('dictation-start'))
    expect(screen.getByTestId('dictation-status').textContent).toBe('Waiting for microphone permission')
    fireEvent.click(screen.getByTestId('dictation-cancel'))
    expect(screen.getByTestId('dictation-start')).toBeTruthy()

    // The user answers the prompt after cancelling: the stream is closed at once.
    await act(async () => { grant({ getTracks: () => [track] }) })
    expect(track.stop).toHaveBeenCalled()
    expect(FakeRecorder.instances).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('releases the microphone and uploads nothing when unmounted mid-recording', async () => {
    const { track } = installMicrophone()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const onTranscript = vi.fn()
    const { unmount } = render(withToast(<DictationButton onTranscript={onTranscript} maxDurationSeconds={60} maxAudioBytes={1000} />))
    fireEvent.click(screen.getByTestId('dictation-start'))
    await screen.findByTestId('dictation-stop')
    unmount()
    expect(track.stop).toHaveBeenCalled()
    expect(FakeRecorder.instances[0].state).toBe('inactive')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onTranscript).not.toHaveBeenCalled()
  })

  it('cancel during transcription drops the transcript that arrives later', async () => {
    installMicrophone()
    let respond
    let aborted = false
    vi.stubGlobal('fetch', vi.fn((url, init) => new Promise((resolve) => {
      respond = resolve
      init.signal.addEventListener('abort', () => { aborted = true })
    })))
    const onTranscript = renderButton()
    fireEvent.click(screen.getByTestId('dictation-start'))
    fireEvent.click(await screen.findByTestId('dictation-stop'))
    await waitFor(() => expect(screen.getByTestId('dictation-status').textContent).toBe('Transcribing'))
    fireEvent.click(screen.getByTestId('dictation-cancel'))
    expect(aborted).toBe(true)
    expect(screen.getByTestId('dictation-start')).toBeTruthy()

    await act(async () => { respond(jsonResponse(200, { text: 'too late' })) })
    expect(onTranscript).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('describes the other microphone failures', () => {
    expect(describeMicrophoneError({ name: 'NotFoundError' })).toMatch(/No microphone was found/)
    expect(describeMicrophoneError({ name: 'NotReadableError' })).toMatch(/Another application/)
    expect(describeMicrophoneError({})).toMatch(/could not be started/)
  })
})

// --- read aloud --------------------------------------------------------------

class FakeAudio {
  static instances = []

  constructor(src) {
    this.src = src
    this.paused = true
    this.playbackRate = 1
    this.defaultPlaybackRate = 1
    FakeAudio.instances.push(this)
  }

  play() { this.paused = false; return Promise.resolve() }
  pause() { this.paused = true }
  removeAttribute() { this.src = '' }
}

describe('read-aloud button', () => {
  let fetchMock

  it('is revealed without hover on touch devices, like the other message buttons', () => {
    const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const touchBlock = css.slice(css.indexOf('@media (hover: none)'))
    const rule = touchBlock.slice(0, touchBlock.indexOf('}'))
    expect(rule).toContain('.read-aloud-button')
    expect(rule).toContain('.copy-message-button')
    expect(rule).toMatch(/opacity:\s*1/)
    // The class the rule targets is the one the idle button actually carries.
    render(withToast(<ReadAloudButton content="Hello" />))
    expect(screen.getByRole('button', { name: 'Read message aloud' }).className).toContain('read-aloud-button')
  })

  beforeEach(() => {
    FakeAudio.instances = []
    vi.stubGlobal('Audio', FakeAudio)
    URL.createObjectURL = vi.fn(() => 'blob:atlas/1')
    URL.revokeObjectURL = vi.fn()
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(['mp3'], { type: 'audio/mpeg' }) })
    vi.stubGlobal('fetch', fetchMock)
  })

  const start = async (content = 'Hello **world**.') => {
    render(withToast(<ReadAloudButton content={content} />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    await screen.findByRole('button', { name: 'Pause reading' })
    return FakeAudio.instances[0]
  }

  it('is not rendered when the capability is unavailable or hidden by preference', () => {
    useChat.mockReturnValue(chatState({ audio: { stt: ON, tts: OFF }, settings: { showTtsButton: true } }))
    const { container, rerender } = render(withToast(<ReadAloudButton content="Hello" />))
    expect(container.innerHTML).toBe('')

    useChat.mockReturnValue(chatState({ settings: { showTtsButton: false } }))
    rerender(withToast(<ReadAloudButton content="Hello" />))
    expect(container.innerHTML).toBe('')
  })

  it('generates audio from the spoken text and plays it', async () => {
    const audio = await start()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/audio/speech')
    expect(JSON.parse(init.body)).toEqual({ input: 'Hello world.' })
    expect(audio.src).toBe('blob:atlas/1')
    expect(audio.paused).toBe(false)
  })

  it('pauses, resumes and stops, releasing the audio', async () => {
    const audio = await start()
    fireEvent.click(screen.getByRole('button', { name: 'Pause reading' }))
    expect(audio.paused).toBe(true)
    fireEvent.click(await screen.findByRole('button', { name: 'Resume reading' }))
    await screen.findByRole('button', { name: 'Pause reading' })
    expect(audio.paused).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Stop reading' }))
    expect(audio.paused).toBe(true)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:atlas/1')
    expect(screen.getByRole('button', { name: 'Read message aloud' })).toBeTruthy()
    // Speed and pause never asked the server again.
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('changes playback speed locally and remembers it', async () => {
    const updateSettings = vi.fn()
    useChat.mockReturnValue(chatState({ updateSettings, settings: { ttsPlaybackRate: 1.25 } }))
    const audio = await start()
    expect(audio.playbackRate).toBe(1.25)
    fireEvent.change(screen.getByRole('combobox', { name: 'Playback speed' }), { target: { value: '1.5' } })
    expect(audio.playbackRate).toBe(1.5)
    expect(updateSettings).toHaveBeenCalledWith({ ttsPlaybackRate: 1.5 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('returns to idle when playback ends', async () => {
    const audio = await start()
    act(() => audio.onended())
    expect(screen.getByRole('button', { name: 'Read message aloud' })).toBeTruthy()
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it('shows a loading state that can be cancelled', async () => {
    let aborted = false
    fetchMock.mockImplementation((url, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        aborted = true
        reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
      })
    }))
    render(withToast(<ReadAloudButton content="Hello" />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel read aloud' }))
    expect(aborted).toBe(true)
    expect(screen.getByRole('button', { name: 'Read message aloud' })).toBeTruthy()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('reports a server error and a message with nothing to read', async () => {
    fetchMock.mockResolvedValue(jsonResponse(502, { detail: 'The speech service could not be reached.' }))
    render(withToast(<ReadAloudButton content="Hello" />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The speech service could not be reached.'))
    expect(screen.getByRole('button', { name: 'Read message aloud' })).toBeTruthy()
    cleanup()

    fetchMock.mockClear()
    render(withToast(<ReadAloudButton content="![](x.png)" />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/no text to read aloud/))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reads one message at a time: starting another stops the first', async () => {
    let n = 0
    URL.createObjectURL = vi.fn(() => `blob:atlas/${++n}`)
    render(withToast(<><div data-testid="a"><ReadAloudButton content="First reply." /></div><div data-testid="b"><ReadAloudButton content="Second reply." /></div></>))
    const within = id => screen.getByTestId(id)
    fireEvent.click(within('a').querySelector('.read-aloud-button'))
    await waitFor(() => expect(within('a').querySelector('[aria-label="Pause reading"]')).not.toBeNull())
    const first = FakeAudio.instances[0]

    fireEvent.click(within('b').querySelector('.read-aloud-button'))
    await waitFor(() => expect(within('b').querySelector('[aria-label="Pause reading"]')).not.toBeNull())
    expect(first.paused).toBe(true)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:atlas/1')
    expect(within('a').querySelector('.read-aloud-button')).not.toBeNull()
    expect(screen.getAllByRole('button', { name: 'Pause reading' })).toHaveLength(1)
  })

  it('stops and releases the audio when the button is hidden mid-playback', async () => {
    const { rerender } = render(withToast(<ReadAloudButton content="Hello" />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    await screen.findByRole('button', { name: 'Pause reading' })
    const audio = FakeAudio.instances[0]

    // The user switches the button off in Settings while it is playing.
    useChat.mockReturnValue(chatState({ settings: { showTtsButton: false } }))
    rerender(withToast(<ReadAloudButton content="Hello" />))
    expect(audio.paused).toBe(true)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:atlas/1')
    expect(screen.queryByRole('button', { name: 'Pause reading' })).toBeNull()
  })

  it('keeps the generated audio when the browser wants another click to play', async () => {
    const blocked = Object.assign(new Error('blocked'), { name: 'NotAllowedError' })
    const play = vi.spyOn(FakeAudio.prototype, 'play').mockRejectedValueOnce(blocked)
    render(withToast(<ReadAloudButton content="Hello" />))
    fireEvent.click(screen.getByRole('button', { name: 'Read message aloud' }))
    const resume = await screen.findByRole('button', { name: 'Resume reading' })
    expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/audio is ready/i))
    expect(toast.error).not.toHaveBeenCalled()

    fireEvent.click(resume)
    await screen.findByRole('button', { name: 'Pause reading' })
    // Played from what was already generated: no second request.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    play.mockRestore()
  })

  it('sends the conversation level and trims to the server limit', async () => {
    useChat.mockReturnValue(chatState({
      audio: { stt: ON, tts: { ...ON, max_input_chars: 30, allowed_data_classifications: ['Internal'] } },
      complianceLevels: [{ name: 'Internal' }],
      activeComplianceFilter: 'Internal',
    }))
    await start('One short sentence. Another sentence that does not fit.')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ input: 'One short sentence.', compliance_level: 'Internal' })
    expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/longer than the read-aloud limit/))
  })
})
