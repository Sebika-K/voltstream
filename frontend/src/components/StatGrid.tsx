import './StatGrid.css'

export interface Stat {
  label: string
  value: string
}

interface Props {
  stats: Stat[]
}

// A generic grid of stat cards. Originally written just for the fleet
// dashboard (2.6, as "FleetSummaryGrid"); generalized here because the
// battery detail page (2.7) needs the identical visual pattern for one
// battery's numbers instead of the whole fleet's -- same component, just a
// different array of {label, value} pairs passed in.
export function StatGrid({ stats }: Props) {
  return (
    <div className="stat-grid">
      {stats.map((stat) => (
        <div className="stat-card" key={stat.label}>
          <div className="stat-value">{stat.value}</div>
          <div className="stat-label">{stat.label}</div>
        </div>
      ))}
    </div>
  )
}
