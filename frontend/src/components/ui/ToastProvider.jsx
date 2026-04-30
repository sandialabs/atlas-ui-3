import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, AlertTriangle, Info, X } from 'lucide-react'
import { ToastContext, DialogContext } from './toastContext'

const DEFAULT_DURATION_MS = 4000

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const idRef = useRef(0)

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((toast) => {
    const id = ++idRef.current
    const entry = {
      id,
      kind: toast.kind || 'info',
      message: toast.message || '',
      duration: toast.duration ?? DEFAULT_DURATION_MS,
    }
    setToasts((prev) => [...prev, entry])
    if (entry.duration > 0) {
      setTimeout(() => dismiss(id), entry.duration)
    }
    return id
  }, [dismiss])

  const api = {
    success: (message, opts) => push({ ...opts, kind: 'success', message }),
    error: (message, opts) => push({ ...opts, kind: 'error', message }),
    info: (message, opts) => push({ ...opts, kind: 'info', message }),
    dismiss,
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        role="region"
        aria-label="Notifications"
        className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastItem({ toast, onDismiss }) {
  const { kind, message } = toast
  const palette =
    kind === 'success'
      ? 'bg-green-900/90 border-green-600 text-green-100'
      : kind === 'error'
      ? 'bg-red-900/90 border-red-600 text-red-100'
      : 'bg-gray-800/95 border-gray-600 text-gray-100'
  const Icon = kind === 'success' ? CheckCircle2 : kind === 'error' ? AlertTriangle : Info
  return (
    <div
      role={kind === 'error' ? 'alert' : 'status'}
      className={`pointer-events-auto flex items-start gap-2 px-3 py-2 rounded-lg shadow-lg border backdrop-blur ${palette} max-w-md`}
    >
      <Icon className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div className="flex-1 text-sm whitespace-pre-wrap break-words">{message}</div>
      <button
        type="button"
        onClick={onDismiss}
        className="ml-1 text-gray-400 hover:text-gray-200"
        aria-label="Dismiss notification"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dialog (prompt / confirm) — custom-UI replacement for window.prompt and
// window.confirm. Single-instance; one dialog at a time. Usage:
//
//   const dialog = useDialog()
//   const name = await dialog.prompt({ title, label, defaultValue })
//   const ok = await dialog.confirm({ title, message, destructive: true })
//
// Both resolve to null / false when the user dismisses.
// ---------------------------------------------------------------------------

export function DialogProvider({ children }) {
  const [dialog, setDialog] = useState(null)

  const prompt = useCallback((opts) => {
    return new Promise((resolve) => {
      setDialog({
        kind: 'prompt',
        title: opts.title || 'Input required',
        label: opts.label || '',
        placeholder: opts.placeholder || '',
        defaultValue: opts.defaultValue || '',
        secondaryLabel: opts.secondaryLabel,
        secondaryDefault: opts.secondaryDefault || '',
        secondaryPlaceholder: opts.secondaryPlaceholder || '',
        okText: opts.okText || 'OK',
        cancelText: opts.cancelText || 'Cancel',
        required: opts.required ?? true,
        resolve,
      })
    })
  }, [])

  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      setDialog({
        kind: 'confirm',
        title: opts.title || 'Confirm',
        message: opts.message || '',
        okText: opts.okText || 'Confirm',
        cancelText: opts.cancelText || 'Cancel',
        destructive: !!opts.destructive,
        resolve,
      })
    })
  }, [])

  const close = useCallback((value) => {
    setDialog((d) => {
      if (d && d.resolve) d.resolve(value)
      return null
    })
  }, [])

  return (
    <DialogContext.Provider value={{ prompt, confirm }}>
      {children}
      {dialog && <DialogHost dialog={dialog} onClose={close} />}
    </DialogContext.Provider>
  )
}

function DialogHost({ dialog, onClose }) {
  const [primary, setPrimary] = useState(dialog.kind === 'prompt' ? dialog.defaultValue : '')
  const [secondary, setSecondary] = useState(dialog.kind === 'prompt' ? dialog.secondaryDefault : '')
  const inputRef = useRef(null)

  useEffect(() => {
    // Autofocus the primary input / confirm button on mount.
    inputRef.current?.focus()
    if (dialog.kind === 'prompt') inputRef.current?.select?.()
  }, [dialog])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose(dialog.kind === 'confirm' ? false : null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dialog, onClose])

  const submit = (e) => {
    e?.preventDefault?.()
    if (dialog.kind === 'prompt') {
      const value = (primary || '').trim()
      if (dialog.required && !value) return
      if (dialog.secondaryLabel !== undefined) {
        onClose({ value, secondary: (secondary || '').trim() })
      } else {
        onClose(value)
      }
    } else {
      onClose(true)
    }
  }

  const cancel = () => onClose(dialog.kind === 'confirm' ? false : null)

  const okBtnClass =
    dialog.kind === 'confirm' && dialog.destructive
      ? 'bg-red-600 hover:bg-red-700'
      : 'bg-blue-600 hover:bg-blue-700'

  return (
    <div
      className="fixed inset-0 z-[9998] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={cancel}
      role="dialog"
      aria-modal="true"
      aria-label={dialog.title}
    >
      <form
        className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-md p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <h2 className="text-lg font-semibold text-gray-100">{dialog.title}</h2>
        {dialog.kind === 'prompt' ? (
          <div className="space-y-3">
            {dialog.label && (
              <label className="block text-xs uppercase text-gray-400">{dialog.label}</label>
            )}
            <input
              ref={inputRef}
              type="text"
              value={primary}
              onChange={(e) => setPrimary(e.target.value)}
              placeholder={dialog.placeholder}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
            {dialog.secondaryLabel !== undefined && (
              <>
                <label className="block text-xs uppercase text-gray-400">
                  {dialog.secondaryLabel}
                </label>
                <textarea
                  value={secondary}
                  onChange={(e) => setSecondary(e.target.value)}
                  placeholder={dialog.secondaryPlaceholder}
                  rows={2}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500 resize-none"
                />
              </>
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-300 whitespace-pre-wrap">{dialog.message}</p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={cancel}
            className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-gray-100"
          >
            {dialog.cancelText}
          </button>
          <button
            ref={dialog.kind === 'confirm' ? inputRef : null}
            type="submit"
            className={`px-4 py-2 rounded-lg text-sm text-white ${okBtnClass}`}
          >
            {dialog.okText}
          </button>
        </div>
      </form>
    </div>
  )
}

