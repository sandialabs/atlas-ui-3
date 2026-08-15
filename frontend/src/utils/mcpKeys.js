export function findServerConfigForMcpKey(key, serverConfigs = []) {
  if (typeof key !== 'string' || !Array.isArray(serverConfigs)) return null

  return serverConfigs
    .filter(server => {
      if (!server || typeof server.server !== 'string') return false
      const prefix = `${server.server}_`
      return key.startsWith(prefix) && key.length > prefix.length
    })
    .sort((a, b) => b.server.length - a.server.length)[0] || null
}

export function getMcpNameFromKey(key, serverConfigs = []) {
  const server = findServerConfigForMcpKey(key, serverConfigs)
  if (server) return key.slice(server.server.length + 1)

  if (typeof key !== 'string') return ''
  const idx = key.indexOf('_')
  return idx === -1 ? key : key.slice(idx + 1)
}
