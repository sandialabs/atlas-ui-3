/**
 * Folder model for the File Library explorer.
 *
 * The extension sets match atlas/modules/file_storage/listing.py so a folder
 * opened in the browser is the same set the paged API returns.
 */

export const PAGE_SIZE = 50

export const SOURCE_FOLDERS = [
  { id: 'all', label: 'All files', source: 'all', category: 'all' },
  { id: 'uploads', label: 'Uploads', source: 'user', category: 'all' },
  { id: 'generated', label: 'Generated', source: 'tool', category: 'all' },
]

export const TYPE_FOLDERS = [
  { id: 'document', label: 'Documents', source: 'all', category: 'document' },
  { id: 'image', label: 'Images', source: 'all', category: 'image' },
  { id: 'data', label: 'Data', source: 'all', category: 'data' },
  { id: 'code', label: 'Code', source: 'all', category: 'code' },
  { id: 'other', label: 'Other', source: 'all', category: 'other' },
]

export const FOLDERS = [...SOURCE_FOLDERS, ...TYPE_FOLDERS]

const EXTENSIONS = {
  image: ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'],
  code: ['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'cpp', 'c', 'go', 'rs'],
  data: ['json', 'csv', 'xlsx', 'xls'],
  document: ['pdf', 'doc', 'docx', 'txt', 'md'],
}

export function folderById(id) {
  return FOLDERS.find((folder) => folder.id === id) || SOURCE_FOLDERS[0]
}

export function categoryForFilename(filename) {
  const leaf = filename || ''
  const extension = leaf.includes('.') ? leaf.split('.').pop().toLowerCase() : ''
  for (const [category, extensions] of Object.entries(EXTENSIONS)) {
    if (extensions.includes(extension)) return category
  }
  return 'other'
}

export function fileTime(file) {
  if (!file?.last_modified) return null
  const time = new Date(file.last_modified).getTime()
  return Number.isNaN(time) ? null : time
}

export function compareFiles(a, b, sortBy, sortOrder) {
  if (sortBy === 'last_modified') {
    const aTime = fileTime(a)
    const bTime = fileTime(b)
    const aUnknown = aTime == null
    const bUnknown = bTime == null
    if (aUnknown && bUnknown) return 0
    if (aUnknown) return 1
    if (bUnknown) return -1
    return sortOrder === 'asc' ? aTime - bTime : bTime - aTime
  }

  let aVal
  let bVal
  if (sortBy === 'name') {
    aVal = (a.filename || '').toLowerCase()
    bVal = (b.filename || '').toLowerCase()
  } else if (sortBy === 'size') {
    aVal = a.size || 0
    bVal = b.size || 0
  } else {
    return 0
  }
  if (aVal < bVal) return sortOrder === 'asc' ? -1 : 1
  if (aVal > bVal) return sortOrder === 'asc' ? 1 : -1
  return 0
}

export function filterAndSortFiles(files, {
  searchQuery = '',
  source = 'all',
  category = 'all',
  sortBy = 'last_modified',
  sortOrder = 'desc',
} = {}) {
  const query = (searchQuery || '').toLowerCase()
  const filtered = (files || []).filter((file) => {
    if (query) {
      const haystack = `${file.filename || ''} ${file.key || ''}`.toLowerCase()
      if (!haystack.includes(query)) return false
    }
    if (source !== 'all' && file.tags?.source !== source) return false
    if (category !== 'all') {
      const nameCategory = categoryForFilename(file.filename)
      const keyCategory = categoryForFilename(file.key)
      if (nameCategory !== category && keyCategory !== category) return false
    }
    return true
  })
  filtered.sort((a, b) => compareFiles(a, b, sortBy, sortOrder))
  return filtered
}

export function filesQuery({ offset = 0, source = 'all', category = 'all', q = '', limit = PAGE_SIZE } = {}) {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (source === 'user' || source === 'tool') params.set('file_type', source)
  if (category && category !== 'all') params.set('category', category)
  const needle = (q || '').trim()
  if (needle) params.set('q', needle)
  return `/api/files?${params.toString()}`
}
