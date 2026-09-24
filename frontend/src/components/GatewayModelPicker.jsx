import { useEffect, useState } from 'react'
import { Users, Wrench, Eye, Loader2 } from 'lucide-react'
import {
  parseGatewayModelKey,
  rememberTeamLabel,
  rememberLastTeam,
  lastTeamFor,
} from '../utils/gatewayModels'

async function fetchJson(url) {
  const res = await fetch(url)
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`
    throw new Error(detail)
  }
  return body
}

/**
 * Team-then-model selection for one enterprise LiteLLM gateway.
 *
 * The user's LiteLLM teams load when the section is shown; choosing a team
 * loads the models that team may call. Choosing a model selects its gateway
 * model key, which carries the team to the backend with every chat turn.
 */
const GatewayModelPicker = ({ gateway, currentModel, onSelect }) => {
  const current = parseGatewayModelKey(currentModel, [gateway])
  const [teams, setTeams] = useState(null)
  const [teamsError, setTeamsError] = useState(null)
  const [teamId, setTeamId] = useState(current?.teamId || lastTeamFor(gateway.name))
  const [models, setModels] = useState(null)
  const [modelsError, setModelsError] = useState(null)
  const base = `/api/llm/gateways/${encodeURIComponent(gateway.name)}`

  useEffect(() => {
    let cancelled = false
    setTeamsError(null)
    fetchJson(`${base}/teams`)
      .then(body => {
        if (cancelled) return
        const list = body?.teams || []
        setTeams(list)
        // Drop a remembered team the user no longer belongs to.
        setTeamId(prev => (list.some(t => t.team_id === prev) ? prev : ''))
      })
      .catch(err => { if (!cancelled) setTeamsError(err.message) })
    return () => { cancelled = true }
  }, [base])

  useEffect(() => {
    if (!teamId || !teams) {
      setModels(null)
      return
    }
    let cancelled = false
    setModels(null)
    setModelsError(null)
    fetchJson(`${base}/models?team_id=${encodeURIComponent(teamId)}`)
      .then(body => { if (!cancelled) setModels(body?.models || []) })
      .catch(err => { if (!cancelled) setModelsError(err.message) })
    return () => { cancelled = true }
  }, [base, teamId, teams])

  const handleTeamChange = (event) => {
    setTeamId(event.target.value)
    if (event.target.value) rememberLastTeam(gateway.name, event.target.value)
  }

  const handleModelSelect = (model) => {
    const team = (teams || []).find(t => t.team_id === teamId)
    rememberTeamLabel(gateway.name, teamId, team?.label || teamId)
    onSelect(model.name)
  }

  const selectId = `gateway-team-${gateway.name}`

  return (
    <div className="border-t border-gray-600 px-3 py-2 space-y-2" data-testid={`gateway-${gateway.name}`}>
      <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-300">
        <Users className="w-3.5 h-3.5 text-purple-400" />
        <span className="truncate">{gateway.display_name || gateway.name}</span>
      </div>
      {gateway.description && (
        <p className="text-[11px] text-gray-500">{gateway.description}</p>
      )}

      {teamsError ? (
        <p className="text-xs text-red-400" role="alert">Could not load teams: {teamsError}</p>
      ) : teams === null ? (
        <p className="text-xs text-gray-400 flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading teams...
        </p>
      ) : teams.length === 0 ? (
        <p className="text-xs text-gray-400">You are not a member of any team on this gateway.</p>
      ) : (
        <div>
          <label htmlFor={selectId} className="block text-[11px] text-gray-400 mb-1">1. Team</label>
          <select
            id={selectId}
            value={teamId}
            onChange={handleTeamChange}
            className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-gray-200"
          >
            <option value="">Select a team...</option>
            {teams.map(team => (
              <option key={team.team_id} value={team.team_id}>{team.label}</option>
            ))}
          </select>
        </div>
      )}

      {teamId && teams && teams.length > 0 && (
        <div>
          <div className="text-[11px] text-gray-400 mb-1">2. Model</div>
          {modelsError ? (
            <p className="text-xs text-red-400" role="alert">Could not load models: {modelsError}</p>
          ) : models === null ? (
            <p className="text-xs text-gray-400 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> Loading models...
            </p>
          ) : models.length === 0 ? (
            <p className="text-xs text-gray-400">This team has no models available.</p>
          ) : (
            <div className="rounded border border-gray-700">
              {models.map(model => {
                const selected = model.name === currentModel
                return (
                  <button
                    key={model.name}
                    type="button"
                    onClick={() => handleModelSelect(model)}
                    className={`w-full text-left px-2 py-1.5 text-sm flex items-center gap-2 border-b border-gray-700 last:border-b-0 hover:bg-gray-700 ${
                      selected ? 'text-blue-300' : 'text-gray-200'
                    }`}
                    aria-pressed={selected}
                    title={model.model_id}
                  >
                    <span className="truncate">{model.label || model.model_id}</span>
                    <span className="flex items-center gap-1 flex-shrink-0 ml-auto">
                      <Eye className={`w-3.5 h-3.5 ${gateway.supports_vision ? 'text-green-400' : 'text-gray-600'}`} />
                      <Wrench className={`w-3.5 h-3.5 ${gateway.supports_tools !== false ? 'text-blue-400' : 'text-gray-600'}`} />
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default GatewayModelPicker
