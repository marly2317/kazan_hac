<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue';
import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { ApiError, messageFor } from '@/shared/api/errors';
import BrandPanel from '@/shared/ui/BrandPanel.vue';
import PasswordInput from '@/shared/ui/PasswordInput.vue';

import { useSessionStore } from '../application/sessionStore';

/**
 * Registration. Same card as sign-in, one field more.
 *
 * The password rules are Django's configured validators, so they are not restated here: a weak
 * password comes back as a 400 whose `details.errors` lists exactly what failed, and that list is
 * what gets shown.
 */
const session = useSessionStore();
const router = useRouter();

const fullName = ref('');
const email = ref('');
const password = ref('');
const busy = ref(false);
const error = ref('');
const hints = ref<string[]>([]);

async function submit(): Promise<void> {
  if (busy.value) return;
  error.value = '';
  hints.value = [];

  if (!fullName.value.trim() || !email.value.trim() || !password.value) {
    error.value = 'Заполните все поля';
    return;
  }

  busy.value = true;
  try {
    await session.signUp({
      fullName: fullName.value.trim(),
      email: email.value.trim(),
      password: password.value,
    });
    await router.replace('/autofill');
  } catch (cause) {
    if (cause instanceof ApiError) {
      error.value = cause.message;
      // `weak_password` carries the validator messages; everything else has an empty `details`.
      const reasons = cause.details.errors;
      hints.value = Array.isArray(reasons) ? reasons : [];
    } else {
      error.value = messageFor(cause, 'Не удалось зарегистрироваться. Попробуйте ещё раз.');
    }
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
          <h2>Регистрация</h2>
        </div>

        <label>
          Имя и фамилия
          <input
            v-model="fullName"
            name="full_name"
            type="text"
            autocomplete="name"
            placeholder="Иван Иванов"
            :aria-invalid="Boolean(error)"
            required
          />
        </label>

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
          <PasswordInput
            v-model="password"
            autocomplete="new-password"
            placeholder="Не менее 8 символов"
            :invalid="Boolean(error)"
          />
        </label>

        <div v-if="error" role="alert">
          <p class="error-text">{{ error }}</p>
          <ul v-if="hints.length" class="error-text">
            <li v-for="hint in hints" :key="hint">{{ hint }}</li>
          </ul>
        </div>

        <button class="button primary" type="submit" :disabled="busy">
          <LoaderCircle v-if="busy" class="spin" :size="19" />
          {{ busy ? 'Создаём…' : 'Создать учётную запись' }}
        </button>

        <p class="form-switch">
          Уже зарегистрированы?
          <RouterLink class="text-button" to="/login">Войти</RouterLink>
        </p>
      </form>
    </section>
  </main>
</template>
