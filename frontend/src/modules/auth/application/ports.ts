import type { Account, Credentials, Registration } from '../domain/account';

/**
 * The port the auth store depends on. The HTTP implementation lives in `infrastructure`.
 *
 * Declared as an interface so the store can be driven by a fake in a test without a server.
 */
export interface AuthGateway {
  login(credentials: Credentials): Promise<Account>;
  register(registration: Registration): Promise<Account>;
  logout(): Promise<void>;
  /** The account behind the current session, or `null` when there is none. */
  currentAccount(): Promise<Account | null>;
}
