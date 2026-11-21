/**
 * One composer warning row (disconnected, tool/vision capability, team-model
 * hint). The tone picks a generated .atlas-notice-<tone> tint from index.css,
 * which reaches AA in every theme; the contrast fixture mounts this component.
 */
export default function ComposerNotice({ tone, icon: Icon, children, ...props }) {
  return (
    <div
      className={`mb-2 px-3 py-2 atlas-notice-${tone} border rounded-lg flex items-center gap-2 text-sm`}
      {...props}
    >
      {Icon && <Icon className="w-4 h-4 flex-shrink-0" />}
      {children}
    </div>
  )
}
