<script setup lang="ts">
import { LogOut, Package, User, Wand2 } from '@lucide/vue';
import { useRouter } from 'vue-router';

import { useSessionStore } from '@/modules/auth/application/sessionStore';
import { useAutofillStore } from '@/modules/autofill/application/autofillStore';
import { useSearchStore } from '@/modules/catalog/application/searchStore';

/**
 * The signed-in chrome: logo, tabs, account button, sign-out — and a slot for the routed screen.
 */
const session = useSessionStore();
const search = useSearchStore();
const autofill = useAutofillStore();
const router = useRouter();

async function signOut(): Promise<void> {
  await session.signOut();
  // The results belong to the session that just ended; leaving them would show the next
  // person who signs in on this browser the previous one's search.
  search.reset();
  // Описание товара — такие же данные сессии, как и результаты поиска: следующий, кто войдёт в
  // этом браузере, не должен увидеть чужой разбор.
  autofill.reset();
  await router.replace('/login');
}
</script>

<template>
  <div class="app">
    <header class="app-header">
      <RouterLink to="/autofill" aria-label="Автозаполнение СТЕ">
        <img class="logo" src="/assets/logo-mark.png" alt="Портал поставщиков" />
      </RouterLink>
      <span class="header-divider" />

      <nav class="tabs" aria-label="Разделы">
        <RouterLink class="tab" to="/autofill">
          <Wand2 :size="17" aria-hidden="true" />
          <span>Автозаполнение</span>
        </RouterLink>
        <RouterLink class="tab" to="/products">
          <Package :size="17" aria-hidden="true" />
          <span>Товары</span>
        </RouterLink>
        <RouterLink class="tab" to="/account">
          <User :size="17" aria-hidden="true" />
          <span>Аккаунт</span>
        </RouterLink>
      </nav>

      <div class="user-menu">
        <RouterLink
          v-if="session.account"
          v-slot="{ isExactActive, navigate }"
          to="/account"
          custom
        >
          <button
            class="account-button"
            :class="{ active: isExactActive }"
            :aria-current="isExactActive ? 'page' : undefined"
            @click="navigate"
          >
            <span class="avatar" aria-hidden="true">{{ session.account.initials }}</span>
            <span>{{ session.account.fullName }}</span>
          </button>
        </RouterLink>

        <button class="text-button" type="button" @click="signOut">
          <LogOut :size="17" />
          <span>Выход</span>
        </button>
      </div>
    </header>

    <main class="page">
      <RouterView />
    </main>
  </div>
</template>
