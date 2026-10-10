<script setup lang="ts">
import type { Suggestion } from '../domain/analysis';
import { suggestionLabel } from '../domain/analysis';

/**
 * Подсказки под полем — как в адресной строке браузера.
 *
 * Показывается не только слово, но и фраза справочника, откуда оно взято: «зимняя» само по
 * себе не говорит ничего, «Куртка зимняя из кожи» говорит всё — и заодно показывает
 * пользователю, что такая позиция в КТРУ вообще есть.
 */
const props = defineProps<{ items: Suggestion[]; highlighted: number }>();
const emit = defineEmits<{ pick: [string] }>();
</script>

<template>
  <ul
    v-if="props.items.length"
    class="suggestions"
    role="listbox"
    aria-label="Подсказки по справочнику КТРУ"
  >
    <li v-for="(item, index) in props.items" :key="item.word">
      <button
        class="suggestion"
        :class="{ active: index === props.highlighted }"
        type="button"
        role="option"
        :aria-selected="index === props.highlighted"
        @mousedown.prevent="emit('pick', item.word)"
      >
        <span class="suggestion-word">{{ item.word }}</span>
        <span class="suggestion-source muted">{{ item.source }}</span>
        <span class="suggestion-kind muted">{{ suggestionLabel(item.kind) }}</span>
      </button>
    </li>
  </ul>
</template>
