/**
 * The account, as this application understands it.
 *
 * Domain layer: plain types and rules, no `fetch`, no Vue, no backend field names. The gateway in
 * `infrastructure` is what translates the API's `snake_case` payload into this shape, so a
 * rename on the server touches one file.
 */

export const ROLES = ['user', 'admin'] as const;

export type Role = (typeof ROLES)[number];

export type Account = {
  id: string;
  email: string;
  fullName: string;
  /** Up to two letters for the header avatar. Computed by the backend so both agree. */
  initials: string;
  role: Role;
  createdAt: string;
};

export type Credentials = {
  email: string;
  password: string;
};

export type Registration = Credentials & {
  fullName: string;
};

/** Russian label for a role. The API returns the slug; only the UI needs a name. */
const ROLE_LABELS: Record<Role, string> = {
  user: 'Пользователь',
  admin: 'Администратор',
};

export function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}

export function isAdmin(account: Account): boolean {
  return account.role === 'admin';
}

/**
 * Local pre-flight on the login form, so an empty submit does not cost a round trip.
 *
 * Deliberately shallow: the backend owns what a valid address and an acceptable password are, and
 * a second, stricter copy of those rules here would reject input the server would have accepted.
 */
export function credentialsComplete({ email, password }: Credentials): boolean {
  return email.trim().length > 0 && password.length > 0;
}
