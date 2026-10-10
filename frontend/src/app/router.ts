import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';

import { useSessionStore } from '@/modules/auth/application/sessionStore';

import AppShell from './AppShell.vue';

/**
 * Routing and the one access rule the application has.
 *
 * Private screens are nested under `AppShell`, so "signed in" and "has the header" are the same
 * statement, and a screen added later cannot accidentally render without the guard.
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/modules/auth/presentation/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/modules/auth/presentation/RegisterView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: AppShell,
    children: [
      { path: '', redirect: { name: 'autofill' } },
      {
        // Основной сценарий хакатона, поэтому он же — точка входа. Поиск по демо-каталогу
        // остаётся отдельной вкладкой: он показывает исправление опечаток на товарах, но
        // путь «описание → характеристики → код КТРУ» проходится здесь.
        path: 'autofill',
        name: 'autofill',
        component: () => import('@/modules/autofill/presentation/AutofillView.vue'),
      },
      {
        path: 'products',
        name: 'products',
        component: () => import('@/modules/catalog/presentation/ProductsView.vue'),
      },
      {
        path: 'account',
        name: 'account',
        component: () => import('@/modules/account/presentation/AccountView.vue'),
      },
    ],
  },
  // Anything else goes to the entry point rather than showing a 404 for a one-screen app.
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const session = useSessionStore();

  // The session lives in an HttpOnly cookie, so only the server can confirm it. `restore()` is
  // idempotent: it asks once per page load, and later navigations read the cached answer.
  await session.restore();

  if (!to.meta.public && !session.isAuthenticated) {
    // `next` brings the user back to where they aimed once they have signed in.
    return { name: 'login', query: to.fullPath === '/' ? {} : { next: to.fullPath } };
  }

  // A signed-in user has no business on the login form; send them to the main screen instead.
  if (to.meta.public && session.isAuthenticated) {
    return { name: 'autofill' };
  }

  return true;
});
