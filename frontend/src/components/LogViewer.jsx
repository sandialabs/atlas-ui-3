import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { Filter, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useChat } from '../contexts/ChatContext';
import { calculateBackoffDelay } from '../hooks/usePollingWithBackoff';

const DEFAULT_POLL_INTERVAL = 60000; // 60s refresh
const MIN_POLL_INTERVAL = 5000; // 5s minimum to prevent accidental DOS
const MAX_BACKOFF_DELAY = 300000; // 5 min max backoff on errors

export default function LogViewer() {
  const navigate = useNavigate();
  const { user } = useChat();
  const [entries, setEntries] = useState([]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [levelFilter, setLevelFilter] = useState('');
  const [moduleFilter, setModuleFilter] = useState('');
  const [hideViewerRequests, setHideViewerRequests] = useState(true);
  const [hideMiddleware, setHideMiddleware] = useState(false); // State for hiding middleware logs
  const [hideConfigRoutes, setHideConfigRoutes] = useState(false); // State for hiding config_routes get_config calls
  const [hideWebsocketEndpoint, setHideWebsocketEndpoint] = useState(false); // State for hiding websocket_endpoint calls
  const [hideHttpClientCalls, setHideHttpClientCalls] = useState(false); // State for hiding _send_single_request calls
  const [hideDiscoverDataSources, setHideDiscoverDataSources] = useState(false); // State for hiding discover_data_sources calls
  const [hideDiscoverTools, setHideDiscoverTools] = useState(false);
  const [hideLiteLLM, setHideLiteLLM] = useState(true); // State for hiding LiteLLM verbose logs (enabled by default)
  const [showOnlyMetrics, setShowOnlyMetrics] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [quickFiltersCollapsed, setQuickFiltersCollapsed] = useState(false); // State for collapsing Quick Filters
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(false); // State for auto-scroll (default off to start at top)
  const [pollIntervalInput, setPollIntervalInput] = useState(String(DEFAULT_POLL_INTERVAL / 1000)); // Input for poll interval in seconds
  const [pollInterval, setPollInterval] = useState(DEFAULT_POLL_INTERVAL); // Actual poll interval in ms

  const tableContainerRef = useRef(null);
  const isScrolledToBottom = useRef(true); // Track if user has scrolled up
  const timeoutIdRef = useRef(null); // Ref to store timeout ID for backoff polling
  const isInitialLoad = useRef(true); // Track initial load for scroll positioning
  const failureCountRef = useRef(0); // Track consecutive failures for backoff

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (levelFilter) params.append('level_filter', levelFilter);
    if (moduleFilter) params.append('module_filter', moduleFilter);
    try {
      const r = await fetch(`/admin/logs/viewer?${params.toString()}`, {
        headers: { 'X-User-Email': user }
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const newEntries = data.entries || [];
      setEntries(prevEntries => {
        if (autoScrollEnabled && newEntries.length > prevEntries.length) {
          setPage(0);
        }
        return newEntries;
      });
      setError(null);
      failureCountRef.current = 0;
      requestAnimationFrame(() => {
        if (tableContainerRef.current) {
          if (isInitialLoad.current) {
            tableContainerRef.current.scrollTop = 0;
            isInitialLoad.current = false;
          } else if (autoScrollEnabled && isScrolledToBottom.current) {
            tableContainerRef.current.scrollTop = tableContainerRef.current.scrollHeight;
          }
        }
      });
    } catch (err) {
      setError(err);
      failureCountRef.current += 1;
    } finally {
      setLoading(false);
    }
  }, [levelFilter, moduleFilter, autoScrollEnabled, user]);

  // Function to clear all logs
  const clearLogs = useCallback(() => {
    setLoading(true);
    fetch('/admin/logs/clear', {
      method: 'POST', // Correct method as defined in admin_routes.py
      headers: {
        'X-User-Email': user
      }
    })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setEntries([]); // Clear entries locally
        setError(null);
      })
      .catch(err => setError(err))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let mounted = true;

    const scheduleNext = () => {
      if (!mounted) return;
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current);
      const delay = failureCountRef.current > 0
        ? calculateBackoffDelay(failureCountRef.current, 1000, MAX_BACKOFF_DELAY)
        : pollInterval;
      timeoutIdRef.current = setTimeout(async () => {
        if (!mounted) return;
        await fetchLogs();
        scheduleNext();
      }, delay);
    };

    // Fetch immediately, then schedule next
    fetchLogs().then(() => { if (mounted) scheduleNext(); });

    return () => {
      mounted = false;
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current);
    };
  }, [fetchLogs, pollInterval]);

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
      // Enforce minimum poll interval to prevent accidental backend DOS
      const clampedMs = Math.max(intervalInSeconds * 1000, MIN_POLL_INTERVAL);
      setPollInterval(clampedMs);
    } else {
      setPollInterval(DEFAULT_POLL_INTERVAL);
    }
  };

  // Toggle all filters on/off
  const handleToggleAll = () => {
    const anyFilterActive = hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources || hideDiscoverTools || hideLiteLLM;
    const newState = !anyFilterActive;

    setPage(0);
    setHideViewerRequests(newState);
    setHideMiddleware(newState);
    setHideConfigRoutes(newState);
    setHideWebsocketEndpoint(newState);
    setHideHttpClientCalls(newState);
    setHideDiscoverDataSources(newState);
    setHideDiscoverTools(newState);
    setHideLiteLLM(newState);
  };

  // Memoize unique levels extracted from entries
  const levels = useMemo(() => 
    Array.from(new Set(entries.map(e => e.level))).sort(),
    [entries]
  );

  // Memoize unique modules extracted from entries
  const modules = useMemo(() => 
    Array.from(new Set(entries.map(e => e.module))).sort(),
    [entries]
  );

  // Memoize filtered entries based on all filter states
  const filtered = useMemo(() => entries.filter(e => {
    // Positive filter: show ONLY metrics entries when active
    if (showOnlyMetrics) {
      return e.message && e.message.includes('[METRIC]');
    }

    // Keyword search filter
    if (searchKeyword) {
      const kw = searchKeyword.toLowerCase();
      const searchable = [e.message, e.module, e.function, e.logger, e.level]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!searchable.includes(kw)) return false;
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

    // Hide _discover_tools_for_server calls
    if (hideDiscoverTools && e.function === '_discover_tools_for_server') {
      return false;
    }

    // Hide LiteLLM verbose logs (logger starts with "LiteLLM")
    if (hideLiteLLM && e.logger && e.logger.startsWith('LiteLLM')) {
      return false;
    }

    return true;
  }), [entries, showOnlyMetrics, searchKeyword, hideViewerRequests, hideMiddleware, hideConfigRoutes, hideWebsocketEndpoint, hideHttpClientCalls, hideDiscoverDataSources, hideDiscoverTools, hideLiteLLM]);

  // Memoize paginated results - compute reverse and slice in a single pass
  const paginated = useMemo(() => {
    const reversed = [...filtered].reverse();
    return reversed.slice(page * pageSize, (page + 1) * pageSize);
  }, [filtered, page, pageSize]);

  const totalPages = useMemo(() =>
    Math.ceil(filtered.length / pageSize) || 1,
    [filtered.length, pageSize]
  );

  // Calculate hidden count for badge display
  const hiddenCount = useMemo(() => entries.length - filtered.length, [entries.length, filtered.length]);

  const changePage = (delta) => {
    setPage(p => Math.min(Math.max(0, p + delta), totalPages - 1));
  };

  return (
    <div className="p-4 space-y-4 h-full w-full flex flex-col min-h-0">
      {/* First row: Filters and Action Buttons */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        {/* Left side: Filters and action buttons */}
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-semibold mb-1">Level</label>
            <select value={levelFilter} onChange={e => setLevelFilter(e.target.value)} className="bg-gray-200 dark:bg-gray-700 p-2 rounded text-sm">
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
          <div>
            <label className="block text-xs font-semibold mb-1">Search</label>
            <input
              type="text"
              value={searchKeyword}
              onChange={e => { setPage(0); setSearchKeyword(e.target.value); }}
              placeholder="Filter by keyword..."
              className="bg-gray-200 dark:bg-gray-700 p-2 rounded text-sm w-48"
            />
          </div>
          <button onClick={fetchLogs} className="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded text-sm font-semibold">Refresh</button>
          <button onClick={clearLogs} className="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded text-sm font-semibold">Clear Logs</button>
          <button onClick={() => window.location.href='/admin/logs/download'} className="bg-gray-300 dark:bg-gray-700 hover:bg-gray-400 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-100 px-3 py-2 rounded text-sm font-medium">Download</button>
          {loading && <span className="text-sm text-gray-500">Loading...</span>}
          {error && <span className="text-sm text-red-500">{error.message}</span>}
        </div>

        {/* Right side: Back button */}
        <button
          onClick={() => navigate('/admin')}
          className="flex items-center gap-2 px-4 py-2 bg-gray-600 hover:bg-gray-700 text-white rounded-lg transition-colors text-sm font-semibold"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Admin Dashboard
        </button>
      </div>

      {/* Quick Filters Section */}
      <div className="bg-gray-100 dark:bg-gray-800 rounded-lg">
        {/* Header with collapse and toggle all */}
        <div className="flex items-center justify-between p-4 pb-3">
          <div>
            <button
              onClick={() => setQuickFiltersCollapsed(!quickFiltersCollapsed)}
              className="flex items-center gap-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg px-2 py-1 transition-colors"
            >
              <Filter className="w-4 h-4 text-gray-600 dark:text-gray-200" />
              <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-100">Quick Filters</h3>
              {hiddenCount > 0 && (
                <span className="ml-1 px-2 py-0.5 bg-cyan-600 text-white text-xs font-medium rounded-full">
                  {hiddenCount} hidden
                </span>
              )}
              {quickFiltersCollapsed ? (
                <ChevronDown className="w-4 h-4 text-gray-500 dark:text-gray-300" />
              ) : (
                <ChevronUp className="w-4 h-4 text-gray-500 dark:text-gray-300" />
              )}
            </button>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 ml-6">
              Checked items are hidden from view
            </p>
          </div>

          <button
            onClick={handleToggleAll}
            className="flex items-center gap-2 px-3 py-1.5 bg-cyan-600 hover:bg-cyan-700 text-white rounded-lg text-xs font-medium transition-colors"
            title={`${hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources || hideLiteLLM ? 'Show all entries' : 'Hide noisy entries'}`}
          >
            {hideViewerRequests || hideMiddleware || hideConfigRoutes || hideWebsocketEndpoint || hideHttpClientCalls || hideDiscoverDataSources || hideLiteLLM ? (
              <>
                <ToggleRight className="w-4 h-4" />
                Show All
              </>
            ) : (
              <>
                <ToggleLeft className="w-4 h-4" />
                Hide Noise
              </>
            )}
          </button>
        </div>
        
        {/* Collapsible content */}
        {!quickFiltersCollapsed && (
        <div className="px-4 pb-4">
          <div className="mb-3 flex items-center gap-3 p-2 rounded-lg border border-purple-300 dark:border-purple-600 bg-purple-50 dark:bg-purple-900/20">
            <label className="flex items-center gap-2 text-xs font-semibold text-purple-700 dark:text-purple-300 select-none cursor-pointer">
              <input
                type="checkbox"
                className="accent-purple-600"
                checked={showOnlyMetrics}
                onChange={e => { setPage(0); setShowOnlyMetrics(e.target.checked); }}
              />
              Show Only: Metrics [METRIC]
            </label>
            <span className="text-[10px] text-purple-500 dark:text-purple-400">
              When active, only metric entries are shown
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Hide: HTTP/API Noise */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Hide: HTTP/API Noise</h4>
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

          {/* Hide: Network Requests */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Hide: Network Requests</h4>
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
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideDiscoverTools}
                  onChange={e => { setPage(0); setHideDiscoverTools(e.target.checked); }}
                />
                Tool discovery (_discover_tools_for_server)
              </label>
            </div>
          </div>

          {/* Hide: Admin Interface */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Hide: Admin Interface</h4>
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

          {/* Hide: LLM Logs */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-gray-600 dark:text-gray-200 uppercase tracking-wide">Hide: LLM Logs</h4>
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-100 select-none cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-cyan-600"
                  checked={hideLiteLLM}
                  onChange={e => { setPage(0); setHideLiteLLM(e.target.checked); }}
                />
                LiteLLM verbose logs
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
            min="5"
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
      <div ref={tableContainerRef} className="flex-1 overflow-auto border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100" onScroll={handleScroll}> {/* Added onScroll handler */}
        <table className="w-full text-sm">
          <thead className="bg-gray-100 dark:bg-gray-800 sticky top-0">
            <tr>
              <th className="text-left p-2 font-semibold">Module</th>
              <th className="text-left p-2 font-semibold">Function</th>
              <th className="text-left p-2 font-semibold">Message</th>
              <th className="text-left p-2 font-semibold">Timestamp</th>
              <th className="text-left p-2 font-semibold">Level</th>
              <th className="text-left p-2 font-semibold">Logger</th>
            </tr>
          </thead>
          <tbody>
            {paginated.map((e, idx) => (
              <tr key={idx} className="border-t border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <td className="p-2 font-mono text-[11px] text-gray-800 dark:text-gray-200">{e.module}</td>
                <td className="p-2 font-mono text-[11px] text-gray-700 dark:text-gray-300">{e.function}</td>
                <td className="p-2 text-[12px] whitespace-pre-wrap break-words leading-snug text-gray-900 dark:text-gray-100 max-w-[480px]">{e.message}</td>
                <td className="p-2 font-mono text-[11px] text-gray-600 dark:text-gray-400 whitespace-nowrap">{e.timestamp?.replace('T',' ').replace('Z','')}</td>
                <td className="p-2 font-semibold text-[11px]">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wide ${e.level === 'ERROR' ? 'bg-red-600/20 text-red-700 dark:bg-red-500/30 dark:text-red-200' : e.level === 'WARNING' ? 'bg-yellow-500/20 text-yellow-700 dark:bg-yellow-500/30 dark:text-yellow-100' : 'bg-cyan-500/20 text-cyan-700 dark:bg-cyan-500/30 dark:text-cyan-100'}`}>{e.level}</span>
                </td>
                <td className="p-2 font-mono text-[11px] text-gray-700 dark:text-gray-300">{e.logger}</td>
              </tr>
            ))}
            {loading && entries.length === 0 && (
              [...Array(8)].map((_, i) => (
                <tr key={`skeleton-${i}`} className="border-t border-gray-200 dark:border-gray-700 animate-pulse">
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-20"></div></td>
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-24"></div></td>
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-full"></div></td>
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-32"></div></td>
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-16"></div></td>
                  <td className="p-2"><div className="h-4 bg-gray-300 dark:bg-gray-700 rounded w-24"></div></td>
                </tr>
              ))
            )}
            {!filtered.length && !loading && (
              <tr><td colSpan={6} className="p-4 text-center text-gray-500">No log entries</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
