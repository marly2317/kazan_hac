import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

import { onSessionLapsed } from '@/shared/api/client';

import type { Account, Credentials, Registration } from '../domain/account';
import { HttpAuthGateway } from '../infrastructure/httpAuthGateway';
import type { AuthGateway } from './ports';

/**
 * The session: the application layer of the auth module.
 *
 * It holds the only copy of "who is signed in" and the use cases that change it. Components read
 * `account` and call these actions; they never touch the gateway, which is what keeps `fetch` out
 * of the views.
 *
 * The gateway is swappable (`useSessionStore().useGateway(fake)`) so a test can drive the store
 * without a server.
 */
export const useSessionStore = defineStore('session', () => {
  let gateway: AuthGateway = new HttpAuthGateway();

  const account = ref<Account | null>(null);
  /** True until the first `restore()` settles, so the router does not redirect prematurely. */
  const loading = ref(true);
  const restored = ref(false);

  const isAuthenticated = computed(() => account.value !== null);

  /** Swap the gateway. Test seam only. */
  function useGateway(next: AuthGateway): void {
    gateway = next;
  }

  /**
   * Ask the backend whether this browser already has a session.
   *
   * Called once before the first navigation. The session lives in an HttpOnly cookie the SPA
   * cannot read, so the server is the only authority on whether it is still valid — persisting
   * the account in `localStorage` would mean rendering a signed-in header for a session that
   * expired hours ago.
   */
  async function restore(): Promise<void> {
    if (restored.value) return;
    loading.value = true;
    try {
      account.value = await gateway.currentAccount();
    } catch {
      // A network failure at startup is not a signed-in state. The login screen will surface
      // the problem on the next attempt.
      account.value = null;
    } finally {
      restored.value = true;
      loading.value = false;
    }
  }

  /** Errors propagate: the form shows the backend's own message next to the inputs. */
  async function signIn(credentials: Credentials): Promise<void> {
    account.value = await gateway.login(credentials);
    restored.value = true;
  }

  async function signUp(registration: Registration): Promise<void> {
    account.value = await gateway.register(registration);
    restored.value = true;
  }

  /**
   * Sign out, locally no matter what.
   *
   * The server call can fail — offline, or the session had already expired — but the user asked
   * to leave, so the local state is cleared either way rather than leaving them on a screen whose
   * next request would 403.
   */
  async function signOut(): Promise<void> {
    try {
      await gateway.logout();
    } finally {
      account.value = null;
    }
  }

  /** The backend reported no session on some request from any screen. */
  function forget(): void {
    account.value = null;
  }

  // One subscription for the whole app: any 403 from any call drops the session here, and the
  // router's guard moves the user to the login screen on the next navigation.
  onSessionLapsed(forget);

  return {
    account,
    loading,
    isAuthenticated,
    useGateway,
    restore,
    signIn,
    signUp,
    signOut,
    forget,
  };
});
