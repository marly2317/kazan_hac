<script setup lang="ts">
import { ListChecks } from '@lucide/vue';

import type { NextStep } from '../domain/analysis';

/**
 * «Что добавить, чтобы остался один код».
 *
 * Задача прямо требует: если КТРУ однозначно не определяется, предложить **конкретные шаги**.
 * Характеристики упорядочены бэкендом по тому, насколько сильно они делят остаток, — вопрос, на
 * который все оставшиеся позиции отвечают одинаково, не приближает к ответу. Нажатие дописывает
 * значение в описание, и сужение идёт дальше от него: это тот самый «ввод характеристик», по
 * которому множество кодов сужается поэтапно.
 */
const props = defineProps<{ steps: NextStep[] }>();
const emit = defineEmits<{ add: [string] }>();
</script>

<template>
  <section v-if="props.steps.length" class="panel next-steps">
    <header class="panel-head">
      <h3>Что уточнить</h3>
      <span class="muted">нажмите значение — оно добавится в описание</span>
    </header>

    <ul class="next-list">
      <li v-for="step in props.steps" :key="step.characteristic" class="next-item">
        <p class="next-name">
          <ListChecks :size="15" aria-hidden="true" />
          {{ step.characteristic }}
          <span class="muted">разделит на {{ step.splitsInto }}</span>
        </p>
        <!-- Качественная характеристика: значение — это текст, который можно дописать в
             описание как есть, поэтому кнопка. -->
        <div v-if="!step.isQuantitative" class="next-options">
          <button
            v-for="option in step.options"
            :key="option"
            class="chip actionable"
            type="button"
            @click="emit('add', option)"
          >
            {{ option }}
          </button>
        </div>

        <!-- Количественная: «≥ 0.2 … < 0.4 Килограмм» — это диапазон, а не значение.
             Дописать его в описание нельзя, поэтому показываются границы, а число пользователь
             вводит сам. -->
        <div v-else class="next-ranges">
          <p class="muted">
            укажите значение<template v-if="step.unit"> в единице «{{ step.unit }}»</template>.
            КТРУ допускает:
          </p>
          <ul class="range-list">
            <li v-for="option in step.options" :key="option">{{ option }}</li>
          </ul>
        </div>
      </li>
    </ul>
  </section>
</template>
