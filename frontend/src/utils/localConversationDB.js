/**
 * IndexedDB wrapper for browser-local conversation storage.
 *
 * Provides the same data shape as the server REST API so that
 * useLocalConversationHistory can be a drop-in replacement for
 * useConversationHistory.
 *
 * Every record belongs to the user who saved it (`user_email`), and every
 * call takes that user: one browser profile can be shared by several
 * accounts, and each sees, searches, exports and deletes only its own
 * records (issue #1050). Records saved before ownership was stored have no
 * owner; they are left in place but no account sees them, since nothing says
 * whose they are.
 */

import { classificationOf } from './conversationClassification'

const DB_NAME = 'atlas-chat-local'
const DB_VERSION = 1
const STORE_NAME = 'conversations'

/** Whether `record` belongs to `userEmail`. Unowned (legacy) records never do. */
export function isOwnedBy(record, userEmail) {
  return !!record && !!userEmail && record.user_email === userEmail
}

function requireUser(userEmail) {
  if (!userEmail) throw new Error('A signed-in user is required for local conversation storage')
}

const byUpdatedDesc = (a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')

// Every record `userEmail` owns. Records are filtered here rather than read
// through the user_email index so a caller can never get an unowned one.
function ownedRecords(store, userEmail) {
  return new Promise((resolve, reject) => {
    const all = store.getAll()
    all.onsuccess = () => resolve(all.result.filter((r) => isOwnedBy(r, userEmail)))
    all.onerror = () => reject(all.error)
  })
}

// Run `fn(store)` in one transaction and resolve with its result once the
// transaction has committed.
async function withStore(mode, fn) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode)
    let result
    let failed = false
    const fail = (err) => {
      if (failed) return
      failed = true
      db.close()
      reject(err)
    }
    Promise.resolve(fn(tx.objectStore(STORE_NAME))).then((r) => { result = r }, (err) => {
      try { tx.abort() } catch { /* already finished */ }
      fail(err)
    })
    tx.oncomplete = () => { if (!failed) { db.close(); resolve(result) } }
    tx.onerror = () => fail(tx.error)
    tx.onabort = () => fail(tx.error || new Error('Transaction aborted'))
  })
}

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

/** Save or update a conversation owned by `userEmail`. */
export async function saveConversation(conversation, userEmail) {
  requireUser(userEmail)
  const record = {
    ...conversation,
    user_email: userEmail,
    updated_at: new Date().toISOString(),
    message_count: conversation.messages?.length || 0,
  }
  return withStore('readwrite', (store) => new Promise((resolve, reject) => {
    // Never overwrite a record with the same id that someone else owns
    // (or that predates ownership).
    const existing = store.get(record.id)
    existing.onsuccess = () => {
      if (existing.result && !isOwnedBy(existing.result, userEmail)) {
        reject(new Error('Conversation belongs to another user'))
        return
      }
      store.put(record)
      resolve(record)
    }
    existing.onerror = () => reject(existing.error)
  }))
}

/** List `userEmail`'s conversations sorted by updated_at descending. */
export async function listConversations(userEmail, limit = 50, offset = 0) {
  if (!userEmail) return []
  return withStore('readonly', async (store) => (await ownedRecords(store, userEmail))
    .sort(byUpdatedDesc)
    .slice(offset, offset + limit)
    .map(summaryFromRecord))
}

/**
 * How many conversations `userEmail` has stored.
 *
 * The count is what tells the sidebar "there is an older page" from "that is
 * all of them" (issue #1085), so it is scoped exactly like the listing: only
 * the records this account owns, never another account's and never an
 * unowned legacy record.
 */
export async function countConversations(userEmail) {
  if (!userEmail) return 0
  return withStore('readonly', async (store) => (await ownedRecords(store, userEmail)).length)
}

/** Get one of `userEmail`'s conversations with full messages; null if not theirs. */
export async function getConversation(id, userEmail) {
  if (!userEmail) return null
  return withStore('readonly', (store) => new Promise((resolve, reject) => {
    const req = store.get(id)
    req.onsuccess = () => resolve(isOwnedBy(req.result, userEmail) ? req.result : null)
    req.onerror = () => reject(req.error)
  }))
}

/** Delete one of `userEmail`'s conversations. Returns true if found. */
export async function deleteConversation(id, userEmail) {
  if (!userEmail) return false
  return withStore('readwrite', (store) => new Promise((resolve, reject) => {
    const req = store.get(id)
    req.onsuccess = () => {
      if (!isOwnedBy(req.result, userEmail)) {
        resolve(false)
        return
      }
      store.delete(id)
      resolve(true)
    }
    req.onerror = () => reject(req.error)
  }))
}

/** Delete all of `userEmail`'s conversations. Returns count deleted. */
export async function deleteAllConversations(userEmail) {
  if (!userEmail) return 0
  return withStore('readwrite', (store) => new Promise((resolve, reject) => {
    // Deletes are issued from the request callback, while the transaction is
    // still active.
    const all = store.getAll()
    all.onsuccess = () => {
      const owned = all.result.filter((r) => isOwnedBy(r, userEmail))
      for (const record of owned) store.delete(record.id)
      resolve(owned.length)
    }
    all.onerror = () => reject(all.error)
  }))
}

/** Search `userEmail`'s conversations by title or message content (case-insensitive). */
export async function searchConversations(userEmail, query, limit = 20) {
  const q = (query || '').toLowerCase()
  if (!q) return listConversations(userEmail, limit)
  if (!userEmail) return []
  return withStore('readonly', async (store) => (await ownedRecords(store, userEmail))
    .filter((c) => {
      if ((c.title || '').toLowerCase().includes(q)) return true
      return (c.messages || []).some(
        (m) => (m.content || '').toLowerCase().includes(q)
      )
    })
    .sort(byUpdatedDesc)
    .slice(0, limit)
    .map(summaryFromRecord))
}

/** Export all of `userEmail`'s conversations (full messages included). */
export async function exportAllConversations(userEmail) {
  if (!userEmail) return []
  return withStore('readonly', async (store) => (await ownedRecords(store, userEmail)).sort(byUpdatedDesc))
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
    // The conversation's recorded classification (issue #1042), in the same
    // shape the server listing uses.
    data_classification: classificationOf({ metadata: record.metadata || {} }).level,
    data_classification_state: classificationOf({ metadata: record.metadata || {} }).state,
    _local: true,
  }
}
