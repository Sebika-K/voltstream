export function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`
}

export function formatTemperature(value: number): string {
  return `${value.toFixed(1)}°C`
}

export function formatPower(value: number): string {
  return `${value.toFixed(2)} kW`
}

export function formatEnergy(value: number): string {
  return `${value.toFixed(2)} kWh`
}

// Splits an ISO timestamp into "date, then time" on two lines, for display
// inside a stat card. Pairs with .stat-value's `white-space: pre` rule
// (StatGrid.css) -- that CSS is what makes this literal "\n" render as an
// actual line break instead of being collapsed like normal HTML whitespace.
export function formatDateTimeTwoLine(isoString: string): string {
  const date = new Date(isoString)
  return `${date.toLocaleDateString()}\n${date.toLocaleTimeString()}`
}
