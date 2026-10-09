<script setup lang="ts">
import { LoaderCircle, Search, X } from '@lucide/vue';
import { nextTick, ref, watch } from 'vue';

/**
 * The search line.
 *
 * The input is *not* bound straight to the store: the store rewrites the query to the corrected
 * text when results come back, and a two-way binding would fight the user mid-keystroke. Local
 * state is synced from the store instead, which lets the correction land in the box exactly
 * once — when the search returns.
 */
const props = defineProps<{ modelValue: string; busy?: boolean }>();
const emit = defineEmits<{ submit: [query: string]; clear: [] }>();

const text = ref(props.modelValue);
const field = ref<HTMLInputElement | null>(null);

watch(
  () => props.modelValue,
  (incoming) => {
    // The store corrected the query; show what was actually searched.
    if (incoming !== text.value) text.value = incoming;
  },
);

function submit(): void {
  emit('submit', text.value.trim());
  // Keep focus: the typical next action is refining the query, not clicking elsewhere.
  void nextTick(() => field.value?.focus());
}

function clear(): void {
  text.value = '';
  emit('clear');
  void nextTick(() => field.value?.focus());
}
</script>

<template>
  <form class="search-bar" role="search" @submit.prevent="submit">
    <span class="search-field">
      <Search class="search-icon" :size="19" aria-hidden="true" />
      <input
        ref="field"
        v-model="text"
        type="search"
        name="q"
        autocomplete="off"
        spellcheck="false"
        placeholder="Поиск по каталогу — например, «бумага А4»"
        aria-label="Поиск товаров"
      />
      <button
        v-if="text"
        class="search-clear"
        type="button"
        aria-label="Очистить запрос"
        @click="clear"
      >
        <X :size="17" />
      </button>
    </span>

    <button class="button primary search-submit" type="submit" :disabled="props.busy">
      <LoaderCircle v-if="props.busy" class="spin" :size="18" />
      <span>Найти</span>
    </button>
  </form>
</template>
