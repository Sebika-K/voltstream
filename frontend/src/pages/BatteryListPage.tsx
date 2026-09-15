import { Link } from 'react-router-dom'
import { useBatteries } from '../hooks/useBatteries'

// Roadmap 2.7: battery list. Columns per the Roadmap: ID, SOC, status,
// temperature, power, last seen. Each row links to that battery's detail
// page -- this is the "navigate from fleet view to an individual battery"
// the Roadmap's "done when" describes. A battery with no current_state yet
// (registered but never sent telemetry) shows "--" instead of a number.
export function BatteryListPage() {
  const { data, loading, error } = useBatteries()

  if (loading) {
    return <p>Loading batteries...</p>
  }

  if (error) {
    return (
      <div>
        <p>Could not load batteries: {error}</p>
        <p>
          Is the backend running at <code>http://localhost:8000</code>?
        </p>
      </div>
    )
  }

  if (!data) {
    return null
  }

  return (
    <div>
      <h1>Batteries</h1>
      <p>{data.total} registered</p>
      <table>
        <thead>
          <tr>
            <th>Battery ID</th>
            <th>SOC</th>
            <th>Status</th>
            <th>Temperature</th>
            <th>Power</th>
            <th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {data.batteries.map((battery) => {
            const state = battery.current_state
            return (
              <tr key={battery.battery_id}>
                <td>
                  <Link to={`/batteries/${battery.battery_id}`}>
                    {battery.battery_id}
                  </Link>
                </td>
                <td>{state ? `${state.state_of_charge}%` : '--'}</td>
                <td>{state?.status ?? '--'}</td>
                <td>{state ? `${state.temperature_c}°C` : '--'}</td>
                <td>{state ? `${state.power_kw} kW` : '--'}</td>
                <td>{state ? new Date(state.last_seen).toLocaleString() : 'never'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
