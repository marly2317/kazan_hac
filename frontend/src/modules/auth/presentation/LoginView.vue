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

/**
 * Демонстрационная учётная запись — для стенда, не для развёртывания.
 *
 * Значения приходят из сборки (`VITE_DEMO_EMAIL` / `VITE_DEMO_PASSWORD`) и пусты по умолчанию,
 * поэтому ссылки просто нет, если их не задали. Заполняются они из `DEMO_LOGIN_EMAIL` /
 * `DEMO_LOGIN_PASSWORD` в `infra/.env` — отдельно от `SEED_DEMO_USER`, который создаёт саму
 * учётную запись, потому что Compose не умеет условий. Это значит, что их можно рассогласовать:
 * заполнить их при выключенном `SEED_DEMO_USER` — значит предложить вход в несуществующий
 * аккаунт. Поэтому в `.env.example` они пусты и подписаны.
 *
 * Учётные данные попадают в бандл и видны любому, кто откроет страницу. Это приемлемо ровно
 * потому, что это общеизвестный демо-аккаунт на локальном стенде, и неприемлемо нигде больше.
 */
const demoEmail = import.meta.env.VITE_DEMO_EMAIL ?? '';
const demoPassword = import.meta.env.VITE_DEMO_PASSWORD ?? '';
const hasDemoAccount = Boolean(demoEmail && demoPassword);

/** Заполнить форму демо-данными и сразу войти — на защите каждое нажатие на счету. */
async function useDemoAccount(): Promise<void> {
  email.value = demoEmail;
  password.value = demoPassword;
  await submit();
}

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
    // Back to wherever the guard interrupted, or to the main screen on a direct visit.
    // `/autofill`, not `/products`: the guard already sends signed-in users there, and the two
    // disagreeing meant signing in by hand landed on the demo catalogue instead of the service.
    const next = typeof route.query.next === 'string' ? route.query.next : '/autofill';
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

        <p v-if="hasDemoAccount" class="demo-hint">
          <button class="text-button" type="button" :disabled="busy" @click="useDemoAccount">
            Войти как демонстрационный пользователь
          </button>
          <small class="muted">{{ demoEmail }} — учётная запись стенда</small>
        </p>
      </form>
    </section>
  </main>
</template>
