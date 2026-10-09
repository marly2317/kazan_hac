<script setup lang="ts">
import { ShieldCheck, User } from '@lucide/vue';
import { computed } from 'vue';

import { isAdmin, roleLabel } from '@/modules/auth/domain/account';
import { useSessionStore } from '@/modules/auth/application/sessionStore';

/**
 * What the header's account button opens: the data behind the current session.
 *
 * Read-only. The store already holds the account the router guard loaded, so this screen does no
 * fetching of its own — opening it twice costs nothing.
 */
const session = useSessionStore();

const account = computed(() => session.account);

/** Rendered in the viewer's own locale rather than the server's. */
const registeredAt = computed(() => {
  if (!account.value) return '';
  return new Date(account.value.createdAt).toLocaleString('ru-RU', {
    dateStyle: 'long',
    timeStyle: 'short',
  });
});
</script>

<template>
  <div v-if="account" class="account-page">
    <header class="account-heading">
      <span class="avatar" aria-hidden="true">{{ account.initials }}</span>
      <div>
        <h1>{{ account.fullName }}</h1>
        <p class="muted">Данные учётной записи</p>
      </div>
    </header>

    <div class="card">
      <dl class="field-list">
        <dt>Имя</dt>
        <dd>{{ account.fullName }}</dd>

        <dt>Почта</dt>
        <dd>{{ account.email }}</dd>

        <dt>Роль</dt>
        <dd>
          <span class="role-badge">
            <ShieldCheck v-if="isAdmin(account)" :size="14" />
            <User v-else :size="14" />
            {{ roleLabel(account.role) }}
          </span>
        </dd>

        <dt>Дата регистрации</dt>
        <dd>{{ registeredAt }}</dd>

        <dt>Идентификатор</dt>
        <dd>{{ account.id }}</dd>
      </dl>
    </div>
  </div>
</template>
