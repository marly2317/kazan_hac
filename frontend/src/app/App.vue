<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue';

import { useSessionStore } from '@/modules/auth/application/sessionStore';

/**
 * The root view.
 *
 * Until the first `restore()` settles the app shows a loader rather than a screen: rendering the
 * login form first and replacing it a moment later makes a signed-in reload flash the form.
 */
const session = useSessionStore();
</script>

<template>
  <div v-if="session.loading" class="page-loader">
    <LoaderCircle class="spin" :size="22" />
    <span>Загружаем…</span>
  </div>
  <RouterView v-else />
</template>
