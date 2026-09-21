// Mirrors backend/app/schemas/prediction.py's PredictionResponse exactly
// (Roadmap 4.5, Contract section 47). Same by-hand-sync tradeoff as the
// other types/*.ts files -- no codegen yet.
//
// `prediction_available=false` always comes with a `reason` and every other
// prediction field `null` -- there's no partial prediction (see
// prediction_service.py's BaselinePredictionResult docstring, which this
// mirrors). `predicted_critical_timestamp` stays `string`, not `Date`, for
// the same reason as battery.ts's `last_seen` -- fetch() never parses dates
// automatically.

export interface PredictionResponse {
  battery_id: string
  current_soc: number | null
  critical_soc: number
  prediction_available: boolean
  predicted_minutes_to_critical: number | null
  predicted_critical_timestamp: string | null
  prediction_method: string | null
  model_version: string | null
  reason: string | null
}
