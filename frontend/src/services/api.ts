// Base URL for the backend API, as the *browser* sees it. Set at build time via
// the VITE_API_BASE_URL environment variable (Vite bakes it into the built
// JavaScript). Falls back to the local dev backend when it isn't set, so
// `npm run dev` keeps working with no configuration.
//
// Note this is a URL the person's browser can reach (http://localhost:8000 --
// the port Docker publishes on the host), NOT the Docker-internal name
// http://backend:8000, which only other containers can resolve.
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

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
