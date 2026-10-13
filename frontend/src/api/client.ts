const DEFAULT_BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${DEFAULT_BASE}${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    const text = await response.text()
    throw new ApiError(text || `HTTP ${response.status}`, response.status)
  }
  return response.json() as Promise<T>
}

export function isMockMode(): boolean {
  return import.meta.env.VITE_USE_MOCK === 'true'
}

/** A 404 from the API means the record does not exist on this machine; anything else is a request failure. */
export function isNotFound(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 404
  return err instanceof NotFoundError
}

/** Raised by mock fixtures so mock mode distinguishes not-found from failures the same way the API does. */
export class NotFoundError extends Error {}
