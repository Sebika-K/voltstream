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

// Turns a raw minutes count (Roadmap 4.5's `predicted_minutes_to_critical`,
// always a whole-number-ish float from a rate calculation) into something a
// person reads at a glance -- "5h 0m" rather than "300.41". Rounds to the
// nearest whole minute first, same reasoning as formatPercent/formatPower
// rounding to a fixed number of decimals: the extra precision isn't
// meaningful for a "how long do I have" estimate.
export function formatDuration(totalMinutesRaw: number): string {
  const totalMinutes = Math.round(totalMinutesRaw)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours === 0) {
    return `${minutes}m`
  }
  return `${hours}h ${minutes}m`
}

// Roadmap 4.5's PredictionResponse.reason is one of a fixed set of snake_case
// codes (see backend/app/services/prediction_service.py's REASON_* constants)
// -- this is the one place that translates each into the plain-language
// sentence Contract section 47 requires be shown instead of a fabricated
// number. An unrecognized code (a future reason added on the backend before
// this map is updated) falls back to a still-honest generic message rather
// than showing the raw snake_case string to a user.
const PREDICTION_REASON_TEXT: Record<string, string> = {
  no_current_state: 'No telemetry has been received yet for this battery.',
  battery_not_discharging: 'This battery is not currently discharging.',
  already_at_or_below_critical_soc:
    'This battery is already at or below the critical charge threshold.',
  discharge_rate_too_low:
    'The current discharge rate is too low to estimate a reliable time.',
}

export function formatPredictionReason(reason: string | null): string {
  if (!reason) {
    return 'Prediction unavailable.'
  }
  return PREDICTION_REASON_TEXT[reason] ?? 'Prediction unavailable right now.'
}
