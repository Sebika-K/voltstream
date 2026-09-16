import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAlerts } from '../hooks/useAlerts'
import { resolveAlert } from '../services/alertService'
import type { AlertFilters } from '../services/alertService'
import './AlertsPage.css'

const ALERT_TYPES = [
  'LOW_SOC',
  'HIGH_TEMPERATURE',
  'RAPID_DISCHARGE',
  'VOLTAGE_ANOMALY',
  'DEVICE_OFFLINE',
]

// Roadmap 3.5: display and filter alerts by severity/battery/alert
// type/resolved state, and let an injected fault actually be "investigated"
// (Roadmap's own wording) -- here, that means following the battery_id link
// to that battery's detail page, and being able to manually resolve an
// alert once it's been dealt with (Roadmap 3.4's PATCH endpoint).
//
// Defaults to `resolved: false` -- "what's currently wrong with the fleet"
// is what an operator opening this page almost always wants first, the same
// reasoning the backend's app/services/alert_service.py docstring gives for
// why the API defaults to newest-first rather than requiring a filter at all.
export function AlertsPage() {
  const [severity, setSeverity] = useState('')
  const [alertType, setAlertType] = useState('')
  const [resolved, setResolved] = useState<'unresolved' | 'resolved' | 'all'>('unresolved')
  const [batteryIdInput, setBatteryIdInput] = useState('')
  const [batteryIdFilter, setBatteryIdFilter] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [actionError, setActionError] = useState<string | null>(null)
  const [resolvingId, setResolvingId] = useState<string | null>(null)

  const filters: AlertFilters = {
    severity: severity || undefined,
    alert_type: alertType || undefined,
    battery_id: batteryIdFilter || undefined,
    resolved: resolved === 'all' ? undefined : resolved === 'resolved',
  }

  const { data, loading, error } = useAlerts(filters, refreshKey)

  function handleBatteryIdSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBatteryIdFilter(batteryIdInput.trim())
  }

  async function handleResolve(alertId: string) {
    setActionError(null)
    setResolvingId(alertId)
    try {
      await resolveAlert(alertId)
      setRefreshKey((key) => key + 1)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not resolve alert')
    } finally {
      setResolvingId(null)
    }
  }

  return (
    <div>
      <h1>Alerts</h1>

      <div className="alert-filters">
        <label>
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All</option>
            <option value="WARNING">Warning</option>
            <option value="CRITICAL">Critical</option>
          </select>
        </label>

        <label>
          Type
          <select value={alertType} onChange={(e) => setAlertType(e.target.value)}>
            <option value="">All</option>
            {ALERT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>

        <label>
          State
          <select
            value={resolved}
            onChange={(e) => setResolved(e.target.value as 'unresolved' | 'resolved' | 'all')}
          >
            <option value="unresolved">Unresolved</option>
            <option value="resolved">Resolved</option>
            <option value="all">All</option>
          </select>
        </label>

        <form onSubmit={handleBatteryIdSubmit}>
          <label>
            Battery ID
            <input
              type="text"
              placeholder="e.g. BAT-000001"
              value={batteryIdInput}
              onChange={(e) => setBatteryIdInput(e.target.value)}
            />
          </label>
          <button type="submit">Filter</button>
        </form>
      </div>

      {actionError && <p className="alert-action-error">{actionError}</p>}

      {loading && <p>Loading alerts...</p>}

      {error && (
        <div>
          <p>Could not load alerts: {error}</p>
          <p>
            Is the backend running at <code>http://localhost:8000</code>?
          </p>
        </div>
      )}

      {data && (
        <>
          <p>
            {data.total} alert{data.total === 1 ? '' : 's'} matching these filters
          </p>
          {data.alerts.length === 0 ? (
            <p>Nothing here right now.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Battery</th>
                  <th>Type</th>
                  <th>Timestamp</th>
                  <th>Message</th>
                  <th>State</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((alert) => (
                  <tr key={alert.alert_id} className={alert.resolved ? 'alert-row-resolved' : ''}>
                    <td>
                      <span className={`severity-badge severity-${alert.severity.toLowerCase()}`}>
                        {alert.severity}
                      </span>
                    </td>
                    <td>
                      <Link to={`/batteries/${alert.battery_id}`}>{alert.battery_id}</Link>
                    </td>
                    <td>{alert.alert_type}</td>
                    <td>{new Date(alert.timestamp).toLocaleString()}</td>
                    <td>{alert.message}</td>
                    <td>
                      {alert.resolved
                        ? `Resolved${alert.resolved_at ? ` (${new Date(alert.resolved_at).toLocaleString()})` : ''}`
                        : 'Active'}
                    </td>
                    <td>
                      {!alert.resolved && (
                        <button
                          type="button"
                          disabled={resolvingId === alert.alert_id}
                          onClick={() => handleResolve(alert.alert_id)}
                        >
                          {resolvingId === alert.alert_id ? 'Resolving...' : 'Resolve'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
