<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue';
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { ApiError, messageFor } from '@/shared/api/errors';
import BrandPanel from '@/shared/ui/BrandPanel.vue';
import PasswordInput from '@/shared/ui/PasswordInput.vue';

import { useSessionStore } from '../application/sessionStore';
import { credentialsComplete } from '../domain/account';

/**
 * Sign-in.
 *
 * The backend identifies a user by **email**, so that is what this form asks for. A failed login
 * shows the backend's own message — it is the only side that knows whether the problem was the
 * credentials, a blocked account or a malformed request.
 */
const session = useSessionStore();
const router = useRouter();
const route = useRoute();

const email = ref('');
const password = ref('');
const busy = ref(false);
const error = ref('');

async function submit(): Promise<void> {
  if (busy.value) return;
  error.value = '';

  if (!credentialsComplete({ email: email.value, password: password.value })) {
    error.value = 'Введите почту и пароль';
    return;
  }

  busy.value = true;
  try {
    await session.signIn({ email: email.value.trim(), password: password.value });
    // Back to wherever the guard interrupted, or to the catalogue on a direct visit.
    const next = typeof route.query.next === 'string' ? route.query.next : '/products';
    await router.replace(next);
  } catch (cause) {
    // A 400 here means "wrong credentials"; anything else is worth showing verbatim.
    error.value =
      cause instanceof ApiError && cause.status === 400
        ? cause.message
        : messageFor(cause, 'Не удалось войти. Попробуйте ещё раз.');
    busy.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <BrandPanel />

    <section class="login-content">
      <form class="login-form" novalidate @submit.prevent="submit">
        <div>
          <div class="eyebrow">
            <img src="/assets/ic-headset.png" alt="" />
            Портал поставщиков
          </div>
          <h2>Вход в сервис</h2>
        </div>

        <label>
          Почта
          <input
            v-model="email"
            name="email"
            type="email"
            autocomplete="username"
            placeholder="Введите почту"
            :aria-invalid="Boolean(error)"
            required
          />
        </label>

        <label>
          Пароль
          <PasswordInput v-model="password" :invalid="Boolean(error)" />
        </label>

        <p v-if="error" class="error-text" role="alert">{{ error }}</p>

        <button class="button primary" type="submit" :disabled="busy">
          <LoaderCircle v-if="busy" class="spin" :size="19" />
          {{ busy ? 'Входим…' : 'Войти' }}
        </button>

        <p class="form-switch">
          Нет учётной записи?
          <RouterLink class="text-button" to="/register">Зарегистрироваться</RouterLink>
        </p>
      </form>
    </section>
  </main>
</template>
