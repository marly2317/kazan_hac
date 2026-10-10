<script setup lang="ts">
import { CircleHelp, CircleX, LoaderCircle, Undo2 } from '@lucide/vue';
import { computed } from 'vue';

import { useAutofillStore } from '../application/autofillStore';
import { positionsLeft, stateLabel } from '../domain/analysis';
import CandidateList from './CandidateList.vue';
import CharacteristicsTable from './CharacteristicsTable.vue';
import DescriptionField from './DescriptionField.vue';
import IssueList from './IssueList.vue';
import NarrowingFunnel from './NarrowingFunnel.vue';
import NextSteps from './NextSteps.vue';

/**
 * Экран автозаполнения СТЕ — основной пользовательский сценарий хакатона.
 *
 * Сверху вниз ровно тот путь, который требует задача: описание → что из него извлечено и к чему
 * приведено → как сужались коды → что мешает → какая позиция получилась → что уточнить.
 * Порядок не косметический: пользователь читает сверху вниз и должен за один проход понять, как
 * сервис дошёл от его слов до кода.
 */
const store = useAutofillStore();

const analysis = computed(() => store.analysis);

/** Код показывается как результат только когда сервис в нём уверен. */
const resolvedCode = computed(() => analysis.value?.resolvedCode ?? null);

const summary = computed(() => {
  const current = analysis.value;
  if (!current) return '';
  if (current.state === 'resolved') return 'Описание определяет единственную позицию КТРУ.';
  if (current.state === 'not_found') {
    return 'Ни одна позиция справочника не соответствует описанию достаточно уверенно.';
  }
  return `Подходит ${positionsLeft(current.totalCandidates)} — добавьте характеристику, чтобы сузить.`;
});
</script>

<template>
  <div class="autofill-page">
    <DescriptionField
      :model-value="store.description"
      :busy="store.loading"
      :suggestions="store.suggestions"
      :highlighted="store.highlighted"
      @update:model-value="store.type($event)"
      @submit="store.analyzeNow()"
      @pick="store.acceptSuggestion($event)"
      @move="store.moveHighlight($event)"
      @dismiss="store.clearSuggestions()"
    />

    <p v-if="store.error" class="error-text" role="alert">{{ store.error }}</p>

    <!-- Пользователь отклонил исправление: сервис разбирает написанное как есть, и это должно
         быть видно, иначе непонятно, почему опечатки больше не исправляются. -->
    <p v-if="!store.correctTypos" class="original-notice">
      <Undo2 :size="16" aria-hidden="true" />
      Исправление опечаток выключено — разбирается то, что написано.
      <button class="text-button" type="button" @click="store.applySuggestion('', store.description)">
        Включить снова
      </button>
    </p>

    <div v-if="store.loading && !analysis" class="results-loader">
      <LoaderCircle class="spin" :size="22" />
      <span>Разбираем описание…</span>
    </div>

    <div v-else-if="!store.analysed" class="empty-state">
      <CircleHelp :size="40" aria-hidden="true" />
      <h3>Опишите товар своими словами</h3>
      <p class="muted">
        Сервис определит характеристики, приведёт значения и единицы измерения к справочнику
        КТРУ и поэтапно сузит множество кодов до одного.
      </p>
    </div>

    <template v-else-if="analysis">
      <!-- Итог разбора. Выносится наверх результатов, потому что это ответ на вопрос, с
           которым пользователь пришёл; всё ниже — обоснование этого ответа. -->
      <section class="verdict" :class="analysis.state">
        <div class="verdict-main">
          <p class="verdict-state">{{ stateLabel(analysis.state) }}</p>
          <p v-if="resolvedCode" class="verdict-code">{{ resolvedCode }}</p>
          <p class="verdict-summary muted">{{ summary }}</p>
        </div>
        <p v-if="analysis.state !== 'empty'" class="verdict-confidence">
          <span class="muted">уверенность по наименованию</span>
          <strong>{{ Math.round(analysis.confidence * 100) }}%</strong>
        </p>
      </section>

      <p v-if="analysis.textChanged" class="original-notice">
        <CircleX :size="16" aria-hidden="true" />
        Разобрано как «{{ analysis.corrected }}».
        <button class="text-button" type="button" @click="store.keepOriginal()">
          Искать как написано
        </button>
      </p>

      <div class="autofill-grid">
        <div class="autofill-column">
          <CharacteristicsTable :items="analysis.characteristics" />
          <IssueList
            :issues="store.issues"
            @apply="(from, to) => store.applySuggestion(from, to)"
          />
        </div>
        <div class="autofill-column">
          <NarrowingFunnel :steps="analysis.steps" :total="analysis.totalCandidates" />
          <NextSteps :steps="analysis.nextSteps" @add="store.addDetail($event)" />
        </div>
      </div>

      <CandidateList
        :candidates="analysis.candidates"
        :state="analysis.state"
        :total="analysis.totalCandidates"
      />
    </template>
  </div>
</template>
