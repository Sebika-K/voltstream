import { Link, useParams } from 'react-router-dom'
import { useBatteryDetail } from '../hooks/useBatteryDetail'
import { useBatteryTelemetryHistory } from '../hooks/useBatteryTelemetryHistory'
import { StatGrid } from '../components/StatGrid'
import type { Stat } from '../components/StatGrid'
import {
  formatPercent,
  formatTemperature,
  formatPower,
  formatDateTimeTwoLine,
} from '../lib/format'

// Roadmap 2.7: battery detail. `battery_id` comes from the URL
// (/batteries/:batteryId) via react-router's useParams -- the first page in
// the project whose content depends on the URL instead of being fixed.
// Fetches two things independently (current state/metadata, and recent
// telemetry history) via two separate hooks, since they're two separate
// backend endpoints (2.2's detail route and 2.3's history route) that can
// succeed or fail independently of each other.
export function BatteryDetailPage() {
  const { batteryId } = useParams<{ batteryId: string }>()
  const detail = useBatteryDetail(batteryId ?? '')
  const history = useBatteryTelemetryHistory(batteryId ?? '')

  if (detail.loading) {
    return <p>Loading battery...</p>
  }

  if (detail.error) {
    return (
      <div>
        <p>
          Could not load {batteryId}: {detail.error}
        </p>
        <Link to="/batteries">Back to battery list</Link>
      </div>
    )
  }

  if (!detail.data) {
    return null
  }

  const battery = detail.data
  const state = battery.current_state

  const stats: Stat[] = state
    ? [
        { label: 'SOC', value: formatPercent(state.state_of_charge) },
        { label: 'Status', value: state.status },
        { label: 'Temperature', value: formatTemperature(state.temperature_c) },
        { label: 'Power', value: formatPower(state.power_kw) },
        { label: 'Health', value: formatPercent(state.health_percent) },
        { label: 'Last seen', value: formatDateTimeTwoLine(state.last_seen) },
      ]
    : [{ label: 'Status', value: 'No telemetry yet' }]

  return (
    <div>
      <Link to="/batteries">&larr; Back to battery list</Link>
      <h1>{battery.battery_id}</h1>
      <p>
        {battery.profile_type} · {battery.capacity_kwh} kWh capacity ·{' '}
        {battery.max_power_kw} kW max power
      </p>

      <h2>Current state</h2>
      <StatGrid stats={stats} />

      <h2>Recent history</h2>
      {history.loading && <p>Loading history...</p>}
      {history.error && <p>Could not load history: {history.error}</p>}
      {history.data && history.data.events.length === 0 && (
        <p>No telemetry recorded yet.</p>
      )}
      {history.data && history.data.events.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>SOC</th>
              <th>Temperature</th>
              <th>Power</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {history.data.events.map((event) => (
              <tr key={event.event_id}>
                <td>{new Date(event.timestamp).toLocaleString()}</td>
                <td>{formatPercent(event.state_of_charge)}</td>
                <td>{formatTemperature(event.temperature_c)}</td>
                <td>{formatPower(event.power_kw)}</td>
                <td>{event.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
