import { createContext, useContext, useState, useEffect, useRef } from 'react'
import { useChat } from './ChatContext'

const MarketplaceContext = createContext()

export const MarketplaceProvider = ({ children }) => {
  const { tools, prompts, features } = useChat()
  const [selectedServers, setSelectedServers] = useState(new Set())
  const [complianceLevels, setComplianceLevels] = useState([])
  const [hierarchyMode, setHierarchyMode] = useState('inclusive')
  const initializedRef = useRef(false)
  const knownServersRef = useRef(new Set())

  // Load compliance levels from API
  useEffect(() => {
    if (!features?.compliance_levels) return
    
    fetch('/api/compliance-levels')
      .then(res => res.json())
      .then(data => {
        setComplianceLevels(data.levels || [])
        setHierarchyMode(data.hierarchy_mode || 'inclusive')
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
      try { knownServersRef.current = new Set(JSON.parse(known)) } catch {}
    } else {
      localStorage.setItem('mcp-known-servers', JSON.stringify([]))
      knownServersRef.current = new Set()
    }
  }, [])

  // When tools/prompts change, merge any newly available servers into selection
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

    if (newlyDiscovered.length > 0) {
      // Auto-select only the newly discovered servers; preserve user choices
      setSelectedServers(prev => {
        const next = new Set(prev)
        newlyDiscovered.forEach(s => next.add(s))
        return next
      })
      // Update known servers persistence
      newlyDiscovered.forEach(s => knownServersRef.current.add(s))
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
  
  // Check if a resource is accessible given the user's compliance level and hierarchy
  const isComplianceAccessible = (userLevel, resourceLevel) => {
    // If either is not set, resource is accessible (backward compatibility)
    if (!userLevel || !resourceLevel) return true
    
    // Find level objects
    const userLevelObj = complianceLevels.find(l => l.name === userLevel)
    const resourceLevelObj = complianceLevels.find(l => l.name === resourceLevel)
    
    // If we don't have level info, be permissive
    if (!userLevelObj || !resourceLevelObj) return true
    
    // In inclusive mode, higher or equal levels can access lower levels
    if (hierarchyMode === 'inclusive') {
      return userLevelObj.level >= resourceLevelObj.level
    }
    
    // Exact match mode
    return userLevel === resourceLevel
  }
  
  const getComplianceFilteredTools = (complianceLevel) => {
    if (!complianceLevel) return getFilteredTools()
    return getFilteredTools().filter(tool => {
      // If no compliance_level specified, include in all filters (backward compatible)
      if (!tool.compliance_level) return true
      return isComplianceAccessible(complianceLevel, tool.compliance_level)
    })
  }
  
  const getComplianceFilteredPrompts = (complianceLevel) => {
    if (!complianceLevel) return getFilteredPrompts()
    return getFilteredPrompts().filter(prompt => {
      if (!prompt.compliance_level) return true
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
    hierarchyMode,
    isComplianceAccessible
  }

  return (
    <MarketplaceContext.Provider value={value}>
      {children}
    </MarketplaceContext.Provider>
  )
}

export const useMarketplace = () => {
  const context = useContext(MarketplaceContext)
  if (!context) {
    throw new Error('useMarketplace must be used within MarketplaceProvider')
  }
  return context
}