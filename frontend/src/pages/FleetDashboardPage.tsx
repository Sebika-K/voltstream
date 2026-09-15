import { useFleetSummary } from '../hooks/useFleetSummary'
import { StatGrid } from '../components/StatGrid'
import type { Stat } from '../components/StatGrid'

// Roadmap 2.6: the fleet dashboard. Reads GET /api/v1/fleet/summary and
// shows fleet-wide numbers using the shared StatGrid component (also used
// by the battery detail page as of 2.7).
export function FleetDashboardPage() {
  const { data, loading, error } = useFleetSummary()

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

  if (!data) {
    return null
  }

  const stats: Stat[] = [
    { label: 'Total devices', value: String(data.total_devices) },
    { label: 'Online', value: String(data.online_devices) },
    { label: 'Offline', value: String(data.offline_devices) },
    { label: 'Average SOC', value: `${data.average_soc}%` },
    {
      label: 'Available energy',
      value: `${data.total_available_energy_kwh} kWh`,
    },
    { label: 'Charging', value: String(data.charging_devices) },
    { label: 'Discharging', value: String(data.discharging_devices) },
    { label: 'Idle', value: String(data.idle_devices) },
    { label: 'Active alerts', value: String(data.active_alerts) },
    { label: 'Critical alerts', value: String(data.critical_alerts) },
  ]

  return (
    <div>
      <h1>VoltStream Fleet Dashboard</h1>
      <StatGrid stats={stats} />
    </div>
  )
}
