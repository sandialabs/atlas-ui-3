import { createContext, useContext, useState, useEffect, useRef } from 'react'
import { useChat } from './ChatContext'

const MarketplaceContext = createContext()

export const MarketplaceProvider = ({ children }) => {
  const { tools, prompts, features } = useChat()
  const [selectedServers, setSelectedServers] = useState(new Set())
  const [complianceLevels, setComplianceLevels] = useState([])
  const [complianceMode, setComplianceMode] = useState('explicit_allowlist')
  const initializedRef = useRef(false)
  const knownServersRef = useRef(new Set())

  // Load compliance levels from API
  useEffect(() => {
    if (!features?.compliance_levels) return
    
    fetch('/api/compliance-levels')
      .then(res => res.json())
      .then(data => {
        setComplianceLevels(data.levels || [])
        setComplianceMode(data.mode || 'explicit_allowlist')
      })
      .catch(err => console.error('Failed to load compliance levels:', err))
  }, [features])

  // Load selected servers from localStorage once on mount
  useEffect(() => {
    if (initializedRef.current) return
    initializedRef.current = true
    const stored = localStorage.getItem('mcp-selected-servers')
    if (stored) {
      try {
        const parsed = JSON.parse(stored)
        setSelectedServers(new Set(parsed))
        return
      } catch (error) {
        console.error('Failed to parse stored selected servers:', error)
      }
    }
    // No stored value: start with empty set; we'll merge as tools/prompts load
    setSelectedServers(new Set())
    // Initialize known servers store if missing
    const known = localStorage.getItem('mcp-known-servers')
    if (known) {
      try { knownServersRef.current = new Set(JSON.parse(known)) } catch { /* ignore parse errors */ }
    } else {
      localStorage.setItem('mcp-known-servers', JSON.stringify([]))
      knownServersRef.current = new Set()
    }
  }, [])

  // When tools/prompts change, merge new servers and remove stale ones
  useEffect(() => {
    // Build the complete set of available servers
    const toolServers = tools.map(t => t.server)
    const promptServers = prompts.map(p => p.server)
    const allServers = new Set([...toolServers, ...promptServers])

    // Determine newly discovered servers (not seen before)
    const newlyDiscovered = []
    for (const s of allServers) {
      if (!knownServersRef.current.has(s)) newlyDiscovered.push(s)
    }

    // Remove stale servers that no longer exist in config
    setSelectedServers(prev => {
      let next = new Set(prev)
      let changed = false

      // Add newly discovered servers
      if (newlyDiscovered.length > 0) {
        newlyDiscovered.forEach(s => next.add(s))
        changed = true
      }

      // Remove servers no longer in config
      for (const s of prev) {
        if (!allServers.has(s)) {
          next.delete(s)
          changed = true
        }
      }

      return changed ? next : prev
    })

    if (newlyDiscovered.length > 0) {
      // Update known servers persistence
      newlyDiscovered.forEach(s => knownServersRef.current.add(s))
    }

    // Prune known servers that no longer exist
    let knownChanged = false
    for (const s of knownServersRef.current) {
      if (!allServers.has(s)) {
        knownServersRef.current.delete(s)
        knownChanged = true
      }
    }
    if (newlyDiscovered.length > 0 || knownChanged) {
      localStorage.setItem('mcp-known-servers', JSON.stringify(Array.from(knownServersRef.current)))
    }
  }, [tools, prompts])

  // Save to localStorage whenever selectedServers changes
  useEffect(() => {
    localStorage.setItem('mcp-selected-servers', JSON.stringify(Array.from(selectedServers)))
  }, [selectedServers])

  const toggleServer = (serverName) => {
    setSelectedServers(prev => {
      const newSet = new Set(prev)
      if (newSet.has(serverName)) {
        newSet.delete(serverName)
        // Clear tool and prompt selections for deselected server
        clearServerMemory(serverName)
      } else {
        newSet.add(serverName)
      }
      return newSet
    })
  }

  const isServerSelected = (serverName) => {
    return selectedServers.has(serverName)
  }

  const selectAllServers = () => {
    const toolServers = tools.map(t => t.server)
    const promptServers = prompts.map(p => p.server)
    const allServers = [...new Set([...toolServers, ...promptServers])]
    setSelectedServers(new Set(allServers))
  }

  const deselectAllServers = () => {
    // Clear memory for all servers before deselecting
    selectedServers.forEach(serverName => clearServerMemory(serverName))
    setSelectedServers(new Set())
  }

  const clearServerMemory = (serverName) => {
    // Clear tool selections for this server from localStorage
    try {
      const savedTools = localStorage.getItem('chatui-selected-tools')
      if (savedTools) {
        const toolSelections = JSON.parse(savedTools)
        const filteredTools = toolSelections.filter(toolKey => !toolKey.startsWith(`${serverName}_`))
        localStorage.setItem('chatui-selected-tools', JSON.stringify(filteredTools))
      }
    } catch (error) {
      console.warn('Failed to clear tool selections for server:', serverName, error)
    }

    // Clear prompt selections for this server from localStorage
    try {
      const savedPrompts = localStorage.getItem('chatui-selected-prompts')
      if (savedPrompts) {
        const promptSelections = JSON.parse(savedPrompts)
        const filteredPrompts = promptSelections.filter(promptKey => !promptKey.startsWith(`${serverName}_`))
        localStorage.setItem('chatui-selected-prompts', JSON.stringify(filteredPrompts))
      }
    } catch (error) {
      console.warn('Failed to clear prompt selections for server:', serverName, error)
    }
  }

  const getFilteredTools = () => {
    return tools.filter(tool => selectedServers.has(tool.server))
  }

  const getFilteredPrompts = () => {
    return prompts.filter(prompt => selectedServers.has(prompt.server))
  }
  
  // Check if a resource is accessible given the user's compliance level using allowlist
  const isComplianceAccessible = (userLevel, resourceLevel) => {
    // If user level is not set, all resources are accessible
    if (!userLevel) return true
    
    // STRICT MODE: If user has selected a compliance level but resource has none, deny access
    if (!resourceLevel) return false
    
    // Find user's compliance level object
    const userLevelObj = complianceLevels.find(l => l.name === userLevel)
    
    // If we don't have level info, deny access (strict)
    if (!userLevelObj) return false
    
    // Check if resource level is in the user's allowed_with list
    return userLevelObj.allowed_with && userLevelObj.allowed_with.includes(resourceLevel)
  }
  
  const getComplianceFilteredTools = (complianceLevel) => {
    if (!complianceLevel) return getFilteredTools()
    return getFilteredTools().filter(tool => {
      // STRICT MODE: When compliance filter is active, only show resources with matching compliance levels
      return isComplianceAccessible(complianceLevel, tool.compliance_level)
    })
  }
  
  const getComplianceFilteredPrompts = (complianceLevel) => {
    if (!complianceLevel) return getFilteredPrompts()
    return getFilteredPrompts().filter(prompt => {
      // STRICT MODE: When compliance filter is active, only show resources with matching compliance levels
      return isComplianceAccessible(complianceLevel, prompt.compliance_level)
    })
  }

  const value = {
    selectedServers,
    toggleServer,
    isServerSelected,
    selectAllServers,
    deselectAllServers,
    getFilteredTools,
    getFilteredPrompts,
    getComplianceFilteredTools,
    getComplianceFilteredPrompts,
    complianceLevels,
    complianceMode,
    isComplianceAccessible
  }

  return (
    <MarketplaceContext.Provider value={value}>
      {children}
    </MarketplaceContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useMarketplace = () => {
  const context = useContext(MarketplaceContext)
  if (!context) {
    throw new Error('useMarketplace must be used within MarketplaceProvider')
  }
  return context
}
