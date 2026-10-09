import { API_ROOT, request, requestVoid, primeCsrf } from '@/shared/api/client';
import { ApiError } from '@/shared/api/errors';

import type { AuthGateway } from '../application/ports';
import type { Account, Credentials, Registration, Role } from '../domain/account';

/** The API's account payload. The only place the backend's field names appear. */
type AccountPayload = {
  id: string;
  email: string;
  full_name: string;
  initials: string;
  role: Role;
  created_at: string;
};

function toAccount(payload: AccountPayload): Account {
  return {
    id: payload.id,
    email: payload.email,
    fullName: payload.full_name,
    initials: payload.initials,
    role: payload.role,
    createdAt: payload.created_at,
  };
}

/** Session auth over HTTP. The backend identifies a user by email, not by a separate username. */
export class HttpAuthGateway implements AuthGateway {
  async login(credentials: Credentials): Promise<Account> {
    // Primed explicitly as well as inside the client: login is the first POST a fresh browser
    // ever sends, and it is the one that must not fail for a missing token.
    await primeCsrf();
    const payload = await request<AccountPayload>(`${API_ROOT}/auth/login/`, {
      method: 'POST',
      body: { email: credentials.email, password: credentials.password },
    });
    return toAccount(payload);
  }

  async register(registration: Registration): Promise<Account> {
    await primeCsrf();
    const payload = await request<AccountPayload>(`${API_ROOT}/auth/register/`, {
      method: 'POST',
      body: {
        email: registration.email,
        password: registration.password,
        full_name: registration.fullName,
      },
    });
    return toAccount(payload);
  }

  logout(): Promise<void> {
    return requestVoid(`${API_ROOT}/auth/logout/`, { method: 'POST' });
  }

  async currentAccount(): Promise<Account | null> {
    try {
      return toAccount(await request<AccountPayload>(`${API_ROOT}/auth/me/`));
    } catch (cause) {
      // "No session" is the expected answer on a cold load, not a failure to report.
      if (cause instanceof ApiError && cause.isUnauthenticated) return null;
      throw cause;
    }
  }
}
