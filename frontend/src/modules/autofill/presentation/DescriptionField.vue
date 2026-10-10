<script setup lang="ts">
import { LoaderCircle, Sparkles } from '@lucide/vue';

import type { Suggestion } from '../domain/analysis';
import SuggestionList from './SuggestionList.vue';

/**
 * Поле свободного описания — вход всего сценария.
 *
 * `textarea`, а не `input`: описание товара — это фраза с характеристиками, и однострочное
 * поле прячет начало того, что пользователь уже написал, ровно когда он дописывает уточнения.
 */
const props = defineProps<{
  modelValue: string;
  busy: boolean;
  suggestions: Suggestion[];
  highlighted: number;
}>();
const emit = defineEmits<{
  'update:modelValue': [string];
  submit: [];
  pick: [string];
  move: [number];
  dismiss: [];
}>();

const EXAMPLES = [
  'куртка зимняя из кожи женская 48 размер',
  'молоток слесарный масса головки 500 г',
  'шкаф для одежды металлический высота 2000 мм',
  'стол письменный ДСП металлический каркас',
];

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value);
}

function onKeydown(event: KeyboardEvent): void {
  // Пока открыт список подсказок, стрелки ходят по нему, а не по тексту — иначе выбрать
  // подсказку с клавиатуры нельзя, и список остаётся украшением для мыши.
  if (props.suggestions.length) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      emit('move', 1);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      emit('move', -1);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      emit('dismiss');
      return;
    }
    // Tab подставляет подсвеченное — привычка из адресной строки и из редакторов кода.
    if (event.key === 'Tab' && props.highlighted >= 0) {
      event.preventDefault();
      emit('pick', props.suggestions[props.highlighted].word);
      return;
    }
  }

  // Enter отправляет, Shift+Enter переводит строку — как в любом поле, где текст короткий, а
  // отправка частая. Но если подсказка выбрана стрелками, Enter подставляет её: пользователь
  // целился в список, а не в кнопку.
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    if (props.highlighted >= 0 && props.suggestions[props.highlighted]) {
      emit('pick', props.suggestions[props.highlighted].word);
      return;
    }
    emit('submit');
  }
}
</script>

<template>
  <section class="description-field">
    <label class="field-label" for="description">Описание товара</label>
    <p class="field-hint muted">
      Напишите своими словами — с опечатками и не до конца. Сервис разберёт описание на
      характеристики и будет сужать коды КТРУ по мере ввода.
    </p>

    <div class="field-row">
      <div class="description-box">
        <textarea
          id="description"
          class="description-input"
          rows="3"
          autocomplete="off"
          :value="props.modelValue"
          placeholder="например: куртка зимняя из кожи женская 48 размер"
          @input="onInput"
          @keydown="onKeydown"
          @blur="emit('dismiss')"
        />
        <SuggestionList
          :items="props.suggestions"
          :highlighted="props.highlighted"
          @pick="emit('pick', $event)"
        />
      </div>
      <button class="button primary analyse" type="button" :disabled="busy" @click="emit('submit')">
        <LoaderCircle v-if="busy" class="spin" :size="18" />
        <Sparkles v-else :size="18" aria-hidden="true" />
        <span>{{ busy ? 'Разбираем…' : 'Разобрать' }}</span>
      </button>
    </div>

    <div v-if="!props.modelValue" class="examples">
      <span class="muted">Примеры:</span>
      <button
        v-for="example in EXAMPLES"
        :key="example"
        class="chip"
        type="button"
        @click="emit('update:modelValue', example), emit('submit')"
      >
        {{ example }}
      </button>
    </div>
  </section>
</template>
