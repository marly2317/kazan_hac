import { ApiError, NetworkError, errorFromResponse } from './errors';

/**
 * The HTTP client every endpoint module goes through.
 *
 * The SPA and the API share one origin — nginx routes `/api/` to Django and everything else to
 * the static bundle, and the Vite dev server proxies `/api` for the same reason — so requests are
 * same-origin: the session cookie rides along with `credentials: 'same-origin'` and there is no
 * CORS preflight to pay.
 *
 * Django checks a CSRF token on every unsafe method. The token lives in a cookie the backend sets
 * and must be echoed back in the `X-CSRFToken` header — read from `document.cookie` on each call
 * rather than cached, because logging out rotates it.
 *
 * Every response that comes back unauthenticated is announced through `onSessionLapsed`, so the
 * auth store can drop to the login screen from wherever the call was made.
 */

export const API_ROOT = '/api/v1';

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const CSRF_COOKIE = 'csrftoken';

function readCookie(name: string): string {
  const prefix = `${name}=`;
  const match = document.cookie.split('; ').find((entry) => entry.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : '';
}

type SessionLapseListener = () => void;
const sessionLapseListeners = new Set<SessionLapseListener>();

/**
 * Subscribe to "the backend says there is no session any more". Returns the unsubscribe.
 *
 * One 403 can arrive from any screen; broadcasting it here keeps the recovery in one place
 * instead of at every call site.
 */
export function onSessionLapsed(listener: SessionLapseListener): () => void {
  sessionLapseListeners.add(listener);
  return () => {
    sessionLapseListeners.delete(listener);
  };
}

type RequestOptions = {
  method?: string;
  /** Serialized as JSON. */
  body?: unknown;
  signal?: AbortSignal;
};

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const method = (options.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = {};

  if (UNSAFE_METHODS.has(method)) {
    // The CSRF cookie outlives the session but not forever; without this, the first write after
    // it expires would fail and the only way out would be a reload.
    await primeCsrf().catch(() => undefined);
    headers['X-CSRFToken'] = readCookie(CSRF_COOKIE);
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      signal: options.signal,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch (cause) {
    // An aborted request is the caller unmounting, not a failure worth reporting as one.
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new NetworkError(cause);
  }

  if (!response.ok) {
    const error = await errorFromResponse(response);
    // `me()` on a cold load is the one expected 403: the store asks whether a session exists and
    // a "no" is an answer, not a lapse. Announcing it would fire the handler during startup.
    if (error.isUnauthenticated && !path.endsWith('/auth/me/')) {
      for (const listener of sessionLapseListeners) listener();
    }
    throw error;
  }
  return response;
}

/** A request whose body we need. `204 No Content` resolves to `undefined`. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** A request whose body we do not need — `logout`, the CSRF priming call. */
export async function requestVoid(path: string, options: RequestOptions = {}): Promise<void> {
  await send(path, options);
}

/**
 * Make sure a CSRF cookie exists before the first unsafe request.
 *
 * A fresh browser has no token, and Django would reject the very first POST — which for a login
 * form is the only POST that matters.
 */
export async function primeCsrf(): Promise<void> {
  if (readCookie(CSRF_COOKIE)) return;
  await requestVoid(`${API_ROOT}/auth/csrf/`);
}

export { ApiError, NetworkError };
