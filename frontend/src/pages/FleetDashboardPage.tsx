import { useRealtimeFleetSummary } from '../hooks/useRealtimeFleetSummary'
import { ConnectionStatus } from '../components/ConnectionStatus'
import { StatGrid } from '../components/StatGrid'
import type { Stat } from '../components/StatGrid'
import { formatPercent, formatEnergy } from '../lib/format'

// Roadmap 2.6 built this as a one-time fetch; Roadmap 3.1 upgrades it to a
// live connection (see hooks/useRealtimeFleetSummary.ts for the full
// REST-snapshot-then-SSE-incremental-updates sequence). The loading/error
// states below only ever apply to that first REST snapshot -- once it's
// loaded, a dropped SSE connection shows up as the ConnectionStatus badge
// changing, not as the whole page reverting to a loading/error state.
export function FleetDashboardPage() {
  const { summary, loading, error, connectionState } = useRealtimeFleetSummary()

  if (loading) {
    return <p>Loading fleet summary...</p>
  }

  if (error) {
    return (
      <div>
        <p>Could not load fleet summary: {error}</p>
        <p>
          Is the backend running at <code>http://localhost:8000</code>?
        </p>
      </div>
    )
  }

  if (!summary) {
    return null
  }

  const stats: Stat[] = [
    { label: 'Total devices', value: String(summary.total_devices) },
    { label: 'Online', value: String(summary.online_devices) },
    { label: 'Offline', value: String(summary.offline_devices) },
    { label: 'Average SOC', value: formatPercent(summary.average_soc) },
    {
      label: 'Available energy',
      value: formatEnergy(summary.total_available_energy_kwh),
    },
    { label: 'Charging', value: String(summary.charging_devices) },
    { label: 'Discharging', value: String(summary.discharging_devices) },
    { label: 'Idle', value: String(summary.idle_devices) },
    { label: 'Active alerts', value: String(summary.active_alerts) },
    { label: 'Critical alerts', value: String(summary.critical_alerts) },
  ]

  return (
    <div>
      <h1>
        VoltStream Fleet Dashboard
        <ConnectionStatus state={connectionState} />
      </h1>
      <StatGrid stats={stats} />
    </div>
  )
}
