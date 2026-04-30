import { createContext, useContext } from 'react'

export const ToastContext = createContext(null)
export const DialogContext = createContext(null)

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    return { success: () => {}, error: () => {}, info: () => {}, dismiss: () => {} }
  }
  return ctx
}

export function useDialog() {
  const ctx = useContext(DialogContext)
  if (!ctx) {
    return {
      prompt: async () => null,
      confirm: async () => false,
    }
  }
  return ctx
}
