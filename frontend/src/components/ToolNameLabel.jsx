import { subagentToolLabel } from '../constants/atlasTools'

/**
 * The name shown on a tool call.
 *
 * Most tools are shown by their own name. The built-in subagent tools are
 * titled by what they do -- "Launch Subagent", "Get Subagent Result" -- with
 * the tool name beside it, so a delegated run is recognisable in a transcript
 * at a glance (issue #1141). A call made under a deprecated name
 * (`atlas_launch`) gets the same title, and still shows the name it was
 * called by.
 */
const ToolNameLabel = ({ toolName, className = '' }) => {
  const label = subagentToolLabel(toolName)
  if (!label) return <span className={className}>{toolName}</span>
  return (
    <span className={className} data-testid="subagent-tool-label">
      {label}
      <span className="ml-1 font-normal text-xs text-gray-400">{toolName}</span>
    </span>
  )
}

export default ToolNameLabel
