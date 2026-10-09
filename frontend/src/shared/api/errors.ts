import type { ApiErrorBody } from './types';

/**
 * A failed API call, carrying the backend's own localized message.
 *
 * The backend returns one envelope for every error and already writes its messages in Russian,
 * so the UI shows `message` rather than inventing its own text per call site. `code` is the
 * stable machine-readable half — branch on that, never on the message.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, string[] | string>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, string[] | string> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /**
   * No usable session: the caller should send the user back to the login screen.
   *
   * A CSRF rejection is also a 403 but means the *token* went stale, not the session — the
   * client re-primes the cookie and the next attempt succeeds, so it must not sign the user out.
   */
  get isUnauthenticated(): boolean {
    if (this.code === 'csrf_failed') return false;
    return this.code === 'not_authenticated' || this.status === 401 || this.status === 403;
  }

  /** Field errors from a 400, flattened to one line per field for display under an input. */
  fieldErrors(): Record<string, string> {
    const flattened: Record<string, string> = {};
    for (const [field, value] of Object.entries(this.details)) {
      flattened[field] = Array.isArray(value) ? value.join(' ') : String(value);
    }
    return flattened;
  }
}

/** The network never reached the backend — offline, DNS, TLS, a dropped connection. */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super('Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.');
    this.name = 'NetworkError';
    this.cause = cause;
  }
}

const GENERIC_MESSAGE = 'Не удалось выполнить запрос. Попробуйте ещё раз.';

/** Build an `ApiError` from a failed response, tolerating a body that is not our envelope. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // A proxy timeout or a 502 from nginx returns HTML, not JSON. Fall through to the default.
  }
  const error = body?.error;
  return new ApiError(
    response.status,
    error?.code ?? 'unknown_error',
    error?.message ?? GENERIC_MESSAGE,
    error?.details ?? {},
  );
}

/** The message to show the user for anything thrown by the API layer. */
export function messageFor(error: unknown, fallback = GENERIC_MESSAGE): string {
  if (error instanceof ApiError || error instanceof NetworkError) return error.message;
  return fallback;
}
