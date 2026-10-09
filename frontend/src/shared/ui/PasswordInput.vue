<script setup lang="ts">
import { Eye, EyeOff } from '@lucide/vue';
import { ref } from 'vue';

/**
 * A password field with a reveal toggle.
 *
 * The toggle is a `type="button"`: inside a form a bare `<button>` submits, so revealing the
 * password would send the form.
 */
const model = defineModel<string>({ required: true });

const props = withDefaults(
  defineProps<{
    autocomplete?: string;
    invalid?: boolean;
    placeholder?: string;
  }>(),
  { autocomplete: 'current-password', invalid: false, placeholder: 'Введите пароль' },
);

const visible = ref(false);
</script>

<template>
  <span class="password-field">
    <input
      v-model="model"
      name="password"
      :type="visible ? 'text' : 'password'"
      :autocomplete="props.autocomplete"
      :placeholder="props.placeholder"
      :aria-invalid="props.invalid"
      required
    />
    <button
      type="button"
      :aria-label="visible ? 'Скрыть пароль' : 'Показать пароль'"
      :aria-pressed="visible"
      @click="visible = !visible"
    >
      <EyeOff v-if="visible" :size="20" />
      <Eye v-else :size="20" />
    </button>
  </span>
</template>
