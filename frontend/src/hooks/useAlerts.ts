import { useAsync } from './useAsync'
import { getAlerts } from '../services/alertService'
import type { AlertFilters } from '../services/alertService'
import type { AlertListResponse } from '../types/alert'

// `refreshKey` isn't a filter -- it's a plain counter the page bumps after a
// successful resolve, so re-running this effect (useAsync's `deps`) refetches
// the list and picks up the change. There's no in-place row update here on
// purpose: the same "just refetch" simplicity useBatteries/useBatteryDetail
// already use, rather than reimplementing PATCH-response-merging logic for
// what's a rarely-clicked action.
export function useAlerts(filters: AlertFilters, refreshKey: number) {
  return useAsync<AlertListResponse>(
    () => getAlerts(filters),
    [filters.severity, filters.battery_id, filters.alert_type, filters.resolved, refreshKey],
  )
}
