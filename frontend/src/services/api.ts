// Base URL for the backend API. Hardcoded to the local dev backend for now --
// this becomes a Vite environment variable (import.meta.env) once the
// frontend needs to point anywhere other than localhost (Roadmap 5.1,
// containerization).
export const API_BASE_URL = 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// Shared by apiGet/apiPatch below: checks the response status and parses
// JSON, throwing ApiError (with the backend's error message, per
// app/core/errors.py's {"error": {"code","message"}} envelope) instead of
// letting callers deal with response.ok themselves.
async function parseOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body: { error?: { message?: string } } | null = await response
      .json()
      .catch(() => null)
    const message = body?.error?.message ?? response.statusText
    throw new ApiError(message, response.status)
  }

  return response.json() as Promise<T>
}

// A thin wrapper around fetch(): builds the full URL, checks the response
// status, and parses JSON -- so every service function below doesn't have to
// repeat that boilerplate.
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`)
  return parseOrThrow<T>(response)
}

// Roadmap 3.4: the first write the frontend does outside of telemetry
// ingestion (which the simulator does, not the browser) -- manually
// resolving an alert via PATCH /api/v1/alerts/{alert_id}. No request body
// needed; the backend's resolve_alert doesn't read one.
export async function apiPatch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: 'PATCH' })
  return parseOrThrow<T>(response)
}
