import { useEffect, useState, useCallback, useRef } from 'react';
import { Filter, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, Activity, Zap, Wrench, File, Database, Server } from 'lucide-react';

const DEFAULT_POLL_INTERVAL = 60000; // 60s refresh

// Category icons and colors
const CATEGORY_INFO = {
  CHAT: { icon: Activity, color: 'text-blue-500', bgColor: 'bg-blue-500/10' },
  LLM: { icon: Zap, color: 'text-purple-500', bgColor: 'bg-purple-500/10' },
  TOOL: { icon: Wrench, color: 'text-orange-500', bgColor: 'bg-orange-500/10' },
  FILE: { icon: File, color: 'text-green-500', bgColor: 'bg-green-500/10' },
  RAG: { icon: Database, color: 'text-cyan-500', bgColor: 'bg-cyan-500/10' },
  SYSTEM: { icon: Server, color: 'text-gray-500', bgColor: 'bg-gray-500/10' }
};

export default function LogViewer() {
  const [entries, setEntries] = useState([]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [levelFilter, setLevelFilter] = useState('');
  const [moduleFilter, setModuleFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState(''); // New: Filter by execution category
  const [viewMode, setViewMode] = useState('flat'); // 'flat' or 'grouped'
  const [hideViewerRequests, setHideViewerRequests] = useState(true);
  const [hideMiddleware, setHideMiddleware] = useState(false);
  const [hideConfigRoutes, setHideConfigRoutes] = useState(false);
  const [hideWebsocketEndpoint, setHideWebsocketEndpoint] = useState(false);
  const [hideHttpClientCalls, setHideHttpClientCalls] = useState(false);
  const [hideDiscoverDataSources, setHideDiscoverDataSources] = useState(false);
  const [quickFiltersCollapsed, setQuickFiltersCollapsed] = useState(false);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);
  const [pollIntervalInput, setPollIntervalInput] = useState(String(DEFAULT_POLL_INTERVAL / 1000));
  const [pollInterval, setPollInterval] = useState(DEFAULT_POLL_INTERVAL);
  const [collapsedConversations, setCollapsedConversations] = useState(new Set());

  const tableContainerRef = useRef(null);
  const isScrolledToBottom = useRef(true);
  const intervalIdRef = useRef(null);

  const fetchLogs = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (levelFilter) params.append('level_filter', levelFilter);
    if (moduleFilter) params.append('module_filter', moduleFilter);
    fetch(`/admin/logs/viewer?${params.toString()}`, {
      headers: {
        'X-User-Email': 'test@test.com'
      }
    })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        const newEntries = data.entries || [];
        // Use functional state update to access previous entries without dependency
        setEntries(prevEntries => {
          // Reset to page 0 if auto-scroll is enabled and new entries were added
          if (autoScrollEnabled && newEntries.length > prevEntries.length) {
            setPage(0);
          }
          return newEntries;
        });
        setError(null);
        // After updating entries, scroll to bottom if auto-scroll is enabled and user hasn't scrolled up
        requestAnimationFrame(() => {
          if (tableContainerRef.current && autoScrollEnabled && isScrolledToBottom.current) {
            tableContainerRef.current.scrollTop = tableContainerRef.current.scrollHeight;
          }
        });
      })
      .catch(err => setError(err))
      .finally(() => setLoading(false));
  }, [levelFilter, moduleFilter, autoScrollEnabled]); // Removed entries.length to prevent infinite loop

  // Function to clear all logs
  const clearLogs = useCallback(() => {
    setLoading(true);
    fetch('/admin/logs/clear', {
      method: 'POST', // Correct method as defined in admin_routes.py
      headers: {
        'X-User-Email': 'test@test.com'
      }
    })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setEntries([]); // Clear entries locally
        setError(null);
      })
      .catch(err => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    // Clear existing interval before setting a new one
    if (intervalIdRef.current) {
      clearInterval(intervalIdRef.current);
    }
    // Fetch logs immediately and then set interval
    fetchLogs();
    intervalIdRef.current = setInterval(fetchLogs, pollInterval);

    // Cleanup interval on component unmount or when pollInterval changes
    return () => clearInterval(intervalIdRef.current);
  }, [fetchLogs, pollInterval]); // Depend on pollInterval

  // Handle scroll event to determine if user has manually scrolled up
  const handleScroll = useCallback(() => {
    if (tableContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = tableContainerRef.current;
      isScrolledToBottom.current = scrollTop + clientHeight >= scrollHeight - 10; // Allow a small buffer
    }
  }, []);

  useEffect(() => {
    const container = tableContainerRef.current;
    if (container) {
      container.addEventListener('scroll', handleScroll);
    }
    return () => {
      if (container) {
        container.removeEventListener('scroll', handleScroll);
      }
    };
  }, [handleScroll]);

  const handlePollIntervalChange = (e) => {
    setPollIntervalInput(e.target.value);
    const intervalInSeconds = parseInt(e.target.value, 10);
    if (!isNaN(intervalInSeconds) && intervalInSeconds > 0) {
      setPollInterval(intervalInSeconds * 1000);
    } else {
      // Reset to default or handle invalid input appropriately
      setPollInterval(DEFAULT_POLL_INTERVAL);
    }
  };

  // Toggle all filters on/off
  const handleToggleAll = () => {
    const anyFilterActive = hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources;
    const newState = !anyFilterActive;
    
    setPage(0);
    setHideViewerRequests(newState);
    setHideMiddleware(newState);
    setHideConfigRoutes(newState);
    setHideWebsocketEndpoint(newState);
    setHideHttpClientCalls(newState);
    setHideDiscoverDataSources(newState);
  };

  // Helper: Extract execution context from log entry
  const getExecutionContext = (entry) => {
    const extras = entry.extras || {};
    return {
      category: extras.extra_log_category || 'SYSTEM',
      phase: extras.extra_execution_phase || '',
      conversationId: extras.extra_conversation_id || entry.extras?.extra_conversation_id,
      requestId: extras.extra_request_id || entry.extras?.extra_request_id,
    };
  };

  // Helper: Group logs by conversation
  const groupByConversation = (logs) => {
    const groups = {};
    logs.forEach(entry => {
      const ctx = getExecutionContext(entry);
      const convId = ctx.conversationId || 'unknown';
      if (!groups[convId]) {
        groups[convId] = [];
      }
      groups[convId].push(entry);
    });
    return groups;
  };

  // Helper: Toggle conversation collapse
  const toggleConversation = (convId) => {
    const newCollapsed = new Set(collapsedConversations);
    if (newCollapsed.has(convId)) {
      newCollapsed.delete(convId);
    } else {
      newCollapsed.add(convId);
    }
    setCollapsedConversations(newCollapsed);
  };

  const levels = Array.from(new Set(entries.map(e => e.level))).sort();
  const modules = Array.from(new Set(entries.map(e => e.module))).sort();
  const categories = Array.from(new Set(entries.map(e => getExecutionContext(e).category))).sort();

  // Apply all filtering logic
  const filtered = entries.filter(e => {
    // Category filter
    if (categoryFilter && getExecutionContext(e).category !== categoryFilter) {
      return false;
    }

    // Hide log viewer requests
    if (hideViewerRequests && (
      (e.message && e.message.includes('GET /admin/logs/viewer')) ||
      (e.path && e.path.includes('/admin/logs/viewer'))
    )) {
      return false;
    }
    
    // Hide middleware calls
    if (hideMiddleware && e.module === 'middleware') {
      return false;
    }
    
    // Hide config routes get_config calls
    if (hideConfigRoutes && e.module === 'config_routes' && e.function === 'get_config') {
      return false;
    }
    
    // Hide websocket endpoint calls
    if (hideWebsocketEndpoint && e.module === 'main' && e.function === 'websocket_endpoint') {
      return false;
    }
    
    // Hide HTTP client _send_single_request calls
    if (hideHttpClientCalls && e.module === '_client' && e.function === '_send_single_request') {
      return false;
    }
    
    // Hide discover_data_sources calls
    if (hideDiscoverDataSources && e.module === 'client' && e.function === 'discover_data_sources') {
      return false;
    }
    
    return true;
  });

  const paginated = filtered.slice().reverse().slice(page * pageSize, (page + 1) * pageSize);
  const totalPages = Math.ceil(filtered.length / pageSize) || 1;

  const changePage = (delta) => {
    setPage(p => Math.min(Math.max(0, p + delta), totalPages - 1));
  };

  return (
    <div className="p-4 space-y-4 h-full flex flex-col">
      {/* First row: Filters and Action Buttons */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end flex-wrap">
        <div>
          <label className="block text-xs font-semibold mb-1 text-gray-700 dark:text-gray-200">View Mode</label>
          <select 
            value={viewMode} 
            onChange={e => setViewMode(e.target.value)} 
            className="bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 p-2 rounded text-sm"
          >
            <option value="flat">Flat List</option>
            <option value="grouped">Execution Path</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold mb-1 text-gray-700 dark:text-gray-200">Category</label>
          <select 
            value={categoryFilter} 
            onChange={e => setCategoryFilter(e.target.value)} 
            className="bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 p-2 rounded text-sm"
          >
            <option value="">All</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold mb-1 text-gray-700 dark:text-gray-200">Level</label>
          <select value={levelFilter} onChange={e => setLevelFilter(e.target.value)} className="bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 p-2 rounded text-sm">
            <option value="">All</option>
            {levels.map(l => <option key={l}>{l}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold mb-1">Module</label>
            <select value={moduleFilter} onChange={e => setModuleFilter(e.target.value)} className="bg-gray-200 dark:bg-gray-700 p-2 rounded text-sm max-w-52">
              <option value="">All</option>
              {modules.map(m => <option key={m}>{m}</option>)}
            </select>
        </div>
        <button onClick={fetchLogs} className="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded text-sm font-semibold">Refresh</button>
        <button onClick={clearLogs} className="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded text-sm font-semibold">Clear Logs</button>
        <button onClick={() => window.location.href='/admin/logs/download'} className="bg-gray-300 dark:bg-gray-700 hover:bg-gray-400 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-100 px-3 py-2 rounded text-sm font-medium">Download</button>
        {loading && <span className="text-sm text-gray-500">Loading...</span>}
        {error && <span className="text-sm text-red-500">{error.message}</span>}
      </div>

      {/* Quick Filters Section */}
      <div className="bg-gray-100 dark:bg-gray-800 rounded-lg">
        {/* Header with collapse and toggle all */}
        <div className="flex items-center justify-between p-4 pb-3">
          <button
            onClick={() => setQuickFiltersCollapsed(!quickFiltersCollapsed)}
            className="flex items-center gap-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg px-2 py-1 transition-colors"
          >
            <Filter className="w-4 h-4 text-gray-600 dark:text-gray-200" />
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-100">Quick Filters</h3>
            {quickFiltersCollapsed ? (
              <ChevronDown className="w-4 h-4 text-gray-500 dark:text-gray-300" />
            ) : (
              <ChevronUp className="w-4 h-4 text-gray-500 dark:text-gray-300" />
            )}
          </button>
          
          <button
            onClick={handleToggleAll}
            className="flex items-center gap-2 px-3 py-1.5 bg-cyan-600 hover:bg-cyan-700 text-white rounded-lg text-xs font-medium transition-colors"
            title={`${hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources ? 'Disable' : 'Enable'} all filters`}
          >
            {hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources ? (
              <ToggleRight className="w-4 h-4" />
            ) : (
              <ToggleLeft className="w-4 h-4" />
            )}
            Toggle All
          </button>
        </div>
        
        {/* Collapsible content */}
        {!quickFiltersCollapsed && (
        <div className="px-4 pb-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* HTTP/API Noise */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">HTTP/API Noise</h4>
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideConfigRoutes}
                  onChange={e => { setPage(0); setHideConfigRoutes(e.target.checked); }}
                />
                Config routes (get_config)
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideWebsocketEndpoint}
                  onChange={e => { setPage(0); setHideWebsocketEndpoint(e.target.checked); }}
                />
                WebSocket connections
              </label>
            </div>
          </div>

          {/* Network Requests */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Network Requests</h4>
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideHttpClientCalls}
                  onChange={e => { setPage(0); setHideHttpClientCalls(e.target.checked); }}
                />
                HTTP client calls
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideDiscoverDataSources}
                  onChange={e => { setPage(0); setHideDiscoverDataSources(e.target.checked); }}
                />
                RAG data source discovery
              </label>
            </div>
          </div>

          {/* Admin Interface */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Admin Interface</h4>
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideViewerRequests}
                  onChange={e => { setPage(0); setHideViewerRequests(e.target.checked); }}
                />
                Log viewer requests
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideMiddleware}
                  onChange={e => { setPage(0); setHideMiddleware(e.target.checked); }}
                />
                Middleware calls
              </label>
            </div>
          </div>
        </div>
        </div>
        )}
      </div>
      
      {/* Settings and Auto-scroll */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-200 select-none cursor-pointer">
            <input type="checkbox" className="accent-cyan-600" checked={autoScrollEnabled} onChange={e => setAutoScrollEnabled(e.target.checked)} />
            Auto-scroll
          </label>
        </div>
        <div className="flex items-center gap-2">
          <label className="block text-xs font-semibold text-gray-700 dark:text-gray-200">Update Frequency (s)</label>
          <input
            type="number"
            value={pollIntervalInput}
            onChange={handlePollIntervalChange}
            min="1"
            className="bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 p-1 rounded text-sm w-16 text-center"
          />
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs">
        <div className="flex items-center gap-2">
          <button onClick={() => changePage(-1)} disabled={page===0} className="px-2 py-1 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded disabled:opacity-50 hover:bg-gray-300 dark:hover:bg-gray-600">Prev</button>
          <span className="text-gray-700 dark:text-gray-200">Page {page+1} / {totalPages}</span>
          <button onClick={() => changePage(1)} disabled={page>=totalPages-1} className="px-2 py-1 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded disabled:opacity-50 hover:bg-gray-300 dark:hover:bg-gray-600">Next</button>
        </div>
        <div>
          <label className="mr-2 text-gray-700 dark:text-gray-200">Page Size</label>
          <select value={pageSize} onChange={e => {setPageSize(parseInt(e.target.value,10)); setPage(0);}} className="bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 p-1 rounded">
            {[50,100,250,500].map(s => <option key={s}>{s}</option>)}
          </select>
        </div>
  <span className="text-gray-600 dark:text-gray-300">Total entries: {entries.length}{filtered.length !== entries.length && ` (showing ${filtered.length})`}</span>
      </div>
      <div ref={tableContainerRef} className="flex-1 overflow-auto border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100" onScroll={handleScroll}>
        {viewMode === 'grouped' ? (
          // Grouped by conversation view
          <div className="p-2 space-y-3">
            {Object.entries(groupByConversation(filtered.slice().reverse())).map(([convId, logs]) => {
              const isCollapsed = collapsedConversations.has(convId);
              const displayId = convId.slice(0, 8);
              return (
                <div key={convId} className="border border-gray-300 dark:border-gray-700 rounded-lg">
                  <div 
                    className="flex items-center gap-2 p-3 bg-gray-100 dark:bg-gray-800 cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-750"
                    onClick={() => toggleConversation(convId)}
                  >
                    {isCollapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
                    <Activity className="w-4 h-4 text-blue-500" />
                    <span className="font-semibold">Conversation {displayId}</span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">({logs.length} events)</span>
                  </div>
                  {!isCollapsed && (
                    <div className="divide-y divide-gray-200 dark:divide-gray-700">
                      {logs.map((e, idx) => {
                        const ctx = getExecutionContext(e);
                        const CategoryIcon = CATEGORY_INFO[ctx.category]?.icon || Server;
                        const catColor = CATEGORY_INFO[ctx.category]?.color || 'text-gray-500';
                        const catBg = CATEGORY_INFO[ctx.category]?.bgColor || 'bg-gray-500/10';
                        
                        return (
                          <div key={idx} className="p-3 hover:bg-gray-50 dark:hover:bg-gray-850">
                            <div className="flex items-start gap-3">
                              <div className={`p-1 rounded ${catBg}`}>
                                <CategoryIcon className={`w-4 h-4 ${catColor}`} />
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 mb-1">
                                  <span className={`text-xs font-semibold px-2 py-0.5 rounded ${catBg} ${catColor}`}>
                                    {ctx.category}
                                  </span>
                                  {ctx.phase && (
                                    <span className="text-xs text-gray-600 dark:text-gray-400">
                                      {ctx.phase}
                                    </span>
                                  )}
                                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wide ${
                                    e.level === 'ERROR' ? 'bg-red-600/20 text-red-700 dark:bg-red-500/30 dark:text-red-200' : 
                                    e.level === 'WARNING' ? 'bg-yellow-500/20 text-yellow-700 dark:bg-yellow-500/30 dark:text-yellow-100' : 
                                    'bg-cyan-500/20 text-cyan-700 dark:bg-cyan-500/30 dark:text-cyan-100'
                                  }`}>
                                    {e.level}
                                  </span>
                                  <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">
                                    {e.timestamp?.replace('T',' ').split('.')[0]}
                                  </span>
                                </div>
                                <div className="text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap break-words">
                                  {e.message}
                                </div>
                                <div className="flex gap-4 mt-1 text-xs text-gray-600 dark:text-gray-400 font-mono">
                                  <span>{e.module}.{e.function}</span>
                                </div>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          // Flat table view  
          <table className="w-full text-sm">
          <thead className="bg-gray-100 dark:bg-gray-800 sticky top-0">
            <tr>
              <th className="text-left p-2 font-semibold">Category</th>
              <th className="text-left p-2 font-semibold">Module</th>
              <th className="text-left p-2 font-semibold">Function</th>
              <th className="text-left p-2 font-semibold">Message</th>
              <th className="text-left p-2 font-semibold">Timestamp</th>
              <th className="text-left p-2 font-semibold">Level</th>
            </tr>
          </thead>
          <tbody>
            {paginated.map((e, idx) => {
              const ctx = getExecutionContext(e);
              const CategoryIcon = CATEGORY_INFO[ctx.category]?.icon || Server;
              const catColor = CATEGORY_INFO[ctx.category]?.color || 'text-gray-500';
              
              return (
                <tr key={idx} className="border-t border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                  <td className="p-2">
                    <div className="flex items-center gap-1">
                      <CategoryIcon className={`w-3 h-3 ${catColor}`} />
                      <span className={`text-[10px] font-semibold ${catColor}`}>{ctx.category}</span>
                    </div>
                  </td>
                  <td className="p-2 font-mono text-[11px] text-gray-800 dark:text-gray-200">{e.module}</td>
                  <td className="p-2 font-mono text-[11px] text-gray-700 dark:text-gray-300">{e.function}</td>
                  <td className="p-2 text-[12px] whitespace-pre-wrap break-words leading-snug text-gray-900 dark:text-gray-100 max-w-[480px]">{e.message}</td>
                  <td className="p-2 font-mono text-[11px] text-gray-600 dark:text-gray-400 whitespace-nowrap">{e.timestamp?.replace('T',' ').split('.')[0]}</td>
                  <td className="p-2 font-semibold text-[11px]">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wide ${e.level === 'ERROR' ? 'bg-red-600/20 text-red-700 dark:bg-red-500/30 dark:text-red-200' : e.level === 'WARNING' ? 'bg-yellow-500/20 text-yellow-700 dark:bg-yellow-500/30 dark:text-yellow-100' : 'bg-cyan-500/20 text-cyan-700 dark:bg-cyan-500/30 dark:text-cyan-100'}`}>{e.level}</span>
                  </td>
                </tr>
              );
            })}
            {!filtered.length && !loading && (
              <tr><td colSpan={6} className="p-4 text-center text-gray-500">No log entries</td></tr>
            )}
          </tbody>
        </table>
        )}
      </div>
    </div>
  );
}
