<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue';
import { computed, ref } from 'vue';

import { useAutofillStore } from '../application/autofillStore';
import type { CardCharacteristic, Question } from '../domain/result';
import { sourceLabel, stageLabel } from '../domain/result';

/**
 * Экран подбора кода КТРУ: описание → тип товара → характеристики → сужение → код.
 * Сверху вниз ровно путь, который требует задача.
 */
const store = useAutofillStore();
const result = computed(() => store.result);
const freeText = ref('');
const editing = ref<string | null>(null);
const editValue = ref('');

const errors = computed(() => (result.value?.signals ?? []).filter((s) => s.severity !== 'info'));
const notes = computed(() => (result.value?.signals ?? []).filter((s) => s.severity === 'info'));

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) void store.analyze();
}

function choose(question: Question, value: string): void {
  if (question.kind === 'family') void store.chooseFamily(value);
  else if (question.kind === 'leaf') void store.chooseLeaf(value);
  else if (question.char_key) void store.answer(question.char_key, value);
}

function sendFreeText(question: Question): void {
  if (!question.char_key || !freeText.value.trim()) return;
  void store.answerText(question.char_key, freeText.value.trim());
  freeText.value = '';
}

function startEdit(item: CardCharacteristic): void {
  editing.value = item.key;
  editValue.value = item.user_value ?? item.value ?? '';
}

function saveEdit(key: string): void {
  if (editValue.value.trim()) void store.edit(key, editValue.value.trim());
  editing.value = null;
}

function copy(code: string): void {
  void navigator.clipboard?.writeText(code);
}
</script>

<template>
  <div class="ste-page">
    <section class="ste-input">
      <label class="ste-label" for="ste-text">Опишите товар своими словами</label>
      <textarea
        id="ste-text"
        :value="store.description"
        rows="3"
        placeholder="Например: болгарка Makita 125 мм 840 Вт от сети"
        @input="store.setDescription(($event.target as HTMLTextAreaElement).value)"
        @keydown="onKey"
      />
      <div class="ste-actions">
        <button class="button" type="button" :disabled="store.loading" @click="store.analyze()">
          <LoaderCircle v-if="store.loading" class="spin" :size="16" />
          Подобрать код КТРУ
        </button>
        <span class="muted">Ctrl+Enter</span>
      </div>
      <p v-if="store.preview.length" class="ste-preview muted">
        Похоже на:
        <span v-for="hit in store.preview" :key="hit.family_id" class="chip">{{ hit.name }}</span>
      </p>
      <p v-if="store.error" class="error-text" role="alert">{{ store.error }}</p>
    </section>

    <template v-if="result">
      <section class="ste-verdict" :class="result.stage">
        <p class="ste-stage">{{ stageLabel(result.stage) }}</p>
        <p v-if="result.final_code" class="ste-code">
          {{ result.final_code }}
          <button class="text-button" type="button" @click="copy(result.final_code)">копировать</button>
        </p>
        <p>{{ result.message }}</p>
        <p v-if="result.family" class="muted">
          Тип товара: <b>{{ result.family.name }}</b> · ОКПД2 {{ result.family.okpd2 }}
          <span v-if="result.family.reason"> · {{ result.family.reason }}</span>
        </p>
      </section>

      <section v-if="result.trace" class="ste-block ste-trace">
        <h3>Как отработала система</h3>
        <ul>
          <li>Тип товара определил: <b>{{ result.trace.family_by }}</b></li>
          <li>
            LLM-1 (выбор семейства): {{ result.trace.llm1_called ? `вызвана, ${result.timings_ms.llm1} мс` : 'не вызывалась на этом шаге' }}
          </li>
          <li>
            LLM-2 (извлечение характеристик): {{ result.trace.llm2_called ? `вызвана, ${result.timings_ms.llm2} мс` : 'не вызывалась на этом шаге' }}
          </li>
          <li>Реранкер: {{ result.trace.reranker ? (result.timings_ms.rerank ? `включён, ${result.timings_ms.rerank} мс` : 'включён (на этом шаге не нужен)') : 'выключен' }} · LLM: {{ result.trace.llm_configured ? 'настроена' : 'не настроена (нет ключа)' }}</li>
          <li v-if="result.trace.llm_error" class="error-text">{{ result.trace.llm_error }}</li>
        </ul>
      </section>

      <section v-if="result.text.fixes.length" class="ste-block">
        <h3>Исправлено в описании</h3>
        <p v-for="fix in result.text.fixes" :key="fix.start + fix.original">
          <s>{{ fix.original }}</s> → <b>{{ fix.corrected }}</b>
          <span class="muted">({{ fix.reason === 'layout' ? 'раскладка' : 'опечатка' }})</span>
        </p>
      </section>

      <section v-if="result.funnel.length" class="ste-block">
        <h3>Сужение кодов КТРУ</h3>
        <ol class="ste-funnel">
          <li v-for="(step, index) in result.funnel" :key="index">
            <span class="muted">{{ step.level }}</span>
            <span>{{ step.label }}</span>
            <b>{{ step.count }}</b>
          </li>
        </ol>
      </section>

      <section v-if="result.question" class="ste-block ste-question">
        <h3>{{ result.question.title }}</h3>
        <p class="muted">{{ result.question.why }}</p>
        <div class="ste-options">
          <button
            v-for="option in result.question.options"
            :key="option.value"
            class="button secondary"
            type="button"
            :disabled="store.loading"
            @click="choose(result.question, option.value)"
          >
            {{ option.label }}
            <span v-if="option.remaining" class="muted">· {{ option.remaining }}</span>
          </button>
          <button
            v-if="result.question.kind === 'char' && result.question.char_key"
            class="text-button"
            type="button"
            @click="store.skip(result.question.char_key)"
          >
            Не знаю
          </button>
        </div>
        <form
          v-if="result.question.kind === 'char' && result.question.allow_free_text"
          class="ste-free"
          @submit.prevent="sendFreeText(result.question)"
        >
          <input v-model="freeText" placeholder="или ответьте своими словами" />
          <button class="button secondary" type="submit" :disabled="store.loading">Ответить</button>
        </form>
      </section>

      <section v-if="errors.length" class="ste-block">
        <h3>Ошибки и несоответствия</h3>
        <ul class="ste-signals">
          <li v-for="(signal, index) in errors" :key="index" :class="signal.severity">
            <p>{{ signal.message }}</p>
            <p v-if="signal.suggestion" class="muted">→ {{ signal.suggestion }}</p>
          </li>
        </ul>
      </section>

      <section v-if="result.card" class="ste-block">
        <h3>
          Характеристики СТЕ
          <span class="muted">{{ result.card.ktru_name }}</span>
        </h3>
        <table class="ste-card">
          <thead>
            <tr><th>Характеристика</th><th>Значение</th><th>По КТРУ</th><th>Источник</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in result.card.characteristics" :key="item.key" :class="{ missing: item.action }">
              <td>
                {{ item.name }}<span v-if="item.required" title="обязательная">*</span>
              </td>
              <td>
                <template v-if="editing === item.key">
                  <input v-model="editValue" @keydown.enter="saveEdit(item.key)" />
                  <button class="text-button" type="button" @click="saveEdit(item.key)">ок</button>
                </template>
                <template v-else>
                  <span :title="item.quote ? `из текста: «${item.quote}»` : ''">
                    {{ item.values.length > 1 ? item.values.join(', ') : (item.value ?? '—') }}
                    <span v-if="item.user_unit && item.unified" class="muted">({{ item.user_value }} {{ item.user_unit }})</span>
                  </span>
                  <span v-if="item.unified" class="chip ok">унифицировано</span>
                  <button class="text-button" type="button" @click="startEdit(item)">изменить</button>
                  <div v-if="item.action && item.options.length" class="ste-options small">
                    <span class="muted">{{ item.action }}:</span>
                    <button
                      v-for="option in item.options.slice(0, 8)"
                      :key="option"
                      class="text-button"
                      type="button"
                      @click="store.answer(item.key, option)"
                    >
                      {{ option }}
                    </button>
                  </div>
                </template>
              </td>
              <td>{{ item.ktru_value ?? '' }} <span v-if="item.unit && !item.ktru_value" class="muted">{{ item.unit }}</span></td>
              <td><span class="chip">{{ sourceLabel(item.source) }}</span></td>
            </tr>
          </tbody>
        </table>
        <div v-if="result.card.extra.length" class="ste-extra">
          <h4>Дополнительные характеристики</h4>
          <p v-for="(item, index) in result.card.extra" :key="index">
            {{ item.name }}: {{ item.value }} {{ item.unit ?? '' }}
            <span v-if="item.matched_from" class="muted">(было: {{ item.matched_from }})</span>
          </p>
        </div>
        <p v-if="result.card.brand || result.card.model" class="muted">
          Бренд: {{ result.card.brand ?? '—' }} · модель: {{ result.card.model ?? '—' }}
        </p>
      </section>

      <section v-if="result.leaves.length > 1" class="ste-block">
        <h3>Оставшиеся позиции КТРУ ({{ result.leaves_count }})</h3>
        <ul class="ste-leaves">
          <li v-for="leaf in result.leaves" :key="leaf.code">
            <button class="text-button" type="button" @click="store.chooseLeaf(leaf.code)">{{ leaf.code }}</button>
            {{ leaf.name }}
            <span v-for="d in leaf.distinct" :key="d.char" class="muted"> · {{ d.char }}: {{ d.value }}</span>
          </li>
        </ul>
      </section>

      <section v-if="notes.length" class="ste-block muted">
        <p v-for="(signal, index) in notes" :key="index">{{ signal.message }}</p>
      </section>

      <p class="muted ste-timings">
        Время: {{ result.timings_ms.total }} мс (реранкер {{ result.timings_ms.rerank ?? 0 }} мс, LLM-1 {{ result.timings_ms.llm1 }} мс, LLM-2
        {{ result.timings_ms.llm2 }} мс)
      </p>
    </template>
  </div>
</template>

<style scoped>
.ste-page { display: grid; gap: 16px; max-width: 1100px; margin: 0 auto; padding: 16px; }
.ste-input textarea { width: 100%; font: inherit; padding: 10px; border: 1px solid var(--line); border-radius: 6px; }
.ste-label { font-weight: 600; display: block; margin-bottom: 6px; }
.ste-actions { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
.chip { display: inline-block; padding: 2px 8px; margin: 2px 4px; border-radius: 10px; background: var(--surface); font-size: 0.85em; }
.chip.ok { background: #dff3ea; color: var(--green); }
.ste-verdict { padding: 12px 16px; border-radius: 8px; background: var(--surface); border-left: 4px solid var(--blue); }
.ste-verdict.done { border-left-color: var(--green); }
.ste-verdict.not_found, .ste-verdict.conflict { border-left-color: var(--red); }
.ste-stage { font-weight: 700; margin: 0; }
.ste-code { font-size: 1.4em; font-weight: 700; color: var(--blue); margin: 4px 0; }
.ste-block { border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; }
.ste-block h3 { margin: 0 0 8px; font-size: 1.05em; }
.ste-funnel { display: flex; flex-wrap: wrap; gap: 8px; list-style: none; padding: 0; margin: 0; }
.ste-funnel li { display: grid; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; min-width: 120px; }
.ste-options { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.ste-options.small { gap: 4px; font-size: 0.85em; }
.button.secondary { background: #fff; color: var(--blue); border: 1px solid var(--blue); }
.ste-free { display: flex; gap: 8px; margin-top: 8px; }
.ste-free input { flex: 1; padding: 6px; }
.ste-signals { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
.ste-signals li { padding: 6px 10px; border-radius: 6px; background: #fff4e5; }
.ste-signals li.error { background: #fdecea; }
.ste-signals p { margin: 0; }
.ste-card { width: 100%; border-collapse: collapse; }
.ste-card th, .ste-card td { text-align: left; padding: 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
.ste-card tr.missing td:first-child { color: var(--red); }
.ste-leaves { padding-left: 18px; }
.ste-timings { font-size: 0.8em; }
.ste-trace ul { margin: 0; padding-left: 18px; }
</style>
