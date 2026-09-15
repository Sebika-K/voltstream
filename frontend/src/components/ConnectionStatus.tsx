import type { ConnectionState } from '../hooks/useRealtimeFleetSummary'
import './ConnectionStatus.css'

// Roadmap 3.1 / Contract section 23: a small, always-visible indicator of
// whether the page is actually receiving live updates right now, so
// "nothing is happening because nothing changed" and "nothing is happening
// because the connection died" never look the same to the person looking
// at the dashboard.
const LABELS: Record<ConnectionState, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
  disconnected: 'Disconnected',
}

export function ConnectionStatus({ state }: { state: ConnectionState }) {
  return (
    <span className={`connection-status connection-status--${state}`}>
      <span className="connection-status__dot" />
      {LABELS[state]}
    </span>
  )
}
