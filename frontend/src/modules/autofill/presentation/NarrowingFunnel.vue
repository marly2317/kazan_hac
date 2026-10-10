<script setup lang="ts">
import { Filter } from '@lucide/vue';

import type { NarrowingStep } from '../domain/analysis';
import { positionsLeft } from '../domain/analysis';

/**
 * Воронка сужения: как сервис дошёл от описания до кода.
 *
 * Отдельная панель, а не строка «найдено N», потому что критерий оценивает именно
 * **наглядность процесса**: пользователь должен видеть, что каждая характеристика отбросила
 * часть позиций, и какая именно. Ширина полосы — доля позиций, оставшихся после шага, от того,
 * с чего шаг начался; шаг, ничего не отбросивший, честно показывает полную полосу.
 */
const props = defineProps<{ steps: NarrowingStep[]; total: number }>();

/** Доля оставшегося от начала шага, в процентах. Первый шаг отсчитывается от себя самого. */
function width(step: NarrowingStep): number {
  const from = step.before || step.after || 1;
  return Math.max(4, Math.round((step.after / from) * 100));
}
</script>

<template>
  <section v-if="props.steps.length" class="panel">
    <header class="panel-head">
      <h3>Сужение кодов КТРУ</h3>
      <span class="muted">{{ positionsLeft(props.total) }} осталось</span>
    </header>

    <ol class="funnel">
      <li v-for="(step, index) in props.steps" :key="`${step.label}-${index}`" class="funnel-step">
        <div class="funnel-head">
          <Filter :size="14" aria-hidden="true" />
          <span class="funnel-label">{{ step.label }}</span>
          <span v-if="step.detail" class="funnel-detail muted">{{ step.detail }}</span>
          <span class="funnel-count">
            <template v-if="step.before">{{ step.before }} →</template>
            <strong>{{ step.after }}</strong>
          </span>
        </div>
        <div class="funnel-bar" role="presentation">
          <span class="funnel-fill" :style="{ width: `${width(step)}%` }" />
        </div>
        <p v-if="step.eliminated" class="funnel-note muted">
          отброшено {{ positionsLeft(step.eliminated) }}
        </p>
        <p v-else-if="step.before" class="funnel-note muted">
          ничего не отброшено — эта характеристика есть у всех оставшихся позиций
        </p>
      </li>
    </ol>
  </section>
</template>
