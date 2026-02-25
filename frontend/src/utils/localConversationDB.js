/**
 * IndexedDB wrapper for browser-local conversation storage.
 *
 * Provides the same data shape as the server REST API so that
 * useLocalConversationHistory can be a drop-in replacement for
 * useConversationHistory.
 */

const DB_NAME = 'atlas-chat-local'
const DB_VERSION = 1
const STORE_NAME = 'conversations'

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = (event) => {
      const db = event.target.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id' })
        store.createIndex('user_email', 'user_email', { unique: false })
        store.createIndex('updated_at', 'updated_at', { unique: false })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

/** Save or update a conversation. */
export async function saveConversation(conversation) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const record = {
      ...conversation,
      updated_at: new Date().toISOString(),
      message_count: conversation.messages?.length || 0,
    }
    store.put(record)
    tx.oncomplete = () => { db.close(); resolve(record) }
    tx.onerror = () => { db.close(); reject(tx.error) }
  })
}

/** List conversations sorted by updated_at descending. */
export async function listConversations(limit = 50, offset = 0) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const all = store.getAll()
    all.onsuccess = () => {
      const sorted = all.result
        .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
        .slice(offset, offset + limit)
        .map(summaryFromRecord)
      db.close()
      resolve(sorted)
    }
    all.onerror = () => { db.close(); reject(all.error) }
  })
}

/** Get a single conversation with full messages. */
export async function getConversation(id) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.get(id)
    req.onsuccess = () => { db.close(); resolve(req.result || null) }
    req.onerror = () => { db.close(); reject(req.error) }
  })
}

/** Delete a single conversation. Returns true if found. */
export async function deleteConversation(id) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    store.delete(id)
    tx.oncomplete = () => { db.close(); resolve(true) }
    tx.onerror = () => { db.close(); reject(tx.error) }
  })
}

/** Delete all conversations. Returns count deleted. */
export async function deleteAllConversations() {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const countReq = store.count()
    countReq.onsuccess = () => {
      const count = countReq.result
      store.clear()
      tx.oncomplete = () => { db.close(); resolve(count) }
    }
    tx.onerror = () => { db.close(); reject(tx.error) }
  })
}

/** Search conversations by title or message content (case-insensitive). */
export async function searchConversations(query, limit = 20) {
  const q = (query || '').toLowerCase()
  if (!q) return listConversations(limit)
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const all = store.getAll()
    all.onsuccess = () => {
      const matches = all.result
        .filter((c) => {
          if ((c.title || '').toLowerCase().includes(q)) return true
          return (c.messages || []).some(
            (m) => (m.content || '').toLowerCase().includes(q)
          )
        })
        .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
        .slice(0, limit)
        .map(summaryFromRecord)
      db.close()
      resolve(matches)
    }
    all.onerror = () => { db.close(); reject(all.error) }
  })
}

/** Export all conversations (full messages included). */
export async function exportAllConversations() {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const all = store.getAll()
    all.onsuccess = () => {
      const sorted = all.result.sort(
        (a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')
      )
      db.close()
      resolve(sorted)
    }
    all.onerror = () => { db.close(); reject(all.error) }
  })
}

/** Convert a full record to the summary shape returned by list endpoints. */
function summaryFromRecord(record) {
  const firstUserMsg = (record.messages || []).find((m) => m.role === 'user')
  return {
    id: record.id,
    title: record.title || 'Untitled',
    preview: firstUserMsg?.content?.substring(0, 100) || '',
    updated_at: record.updated_at,
    created_at: record.created_at,
    message_count: record.message_count || record.messages?.length || 0,
    model: record.model,
    tags: record.tags || [],
    _local: true,
  }
}
