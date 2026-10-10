<script setup lang="ts">
import { AlertTriangle, Info, OctagonAlert } from '@lucide/vue';

import type { Issue } from '../domain/analysis';
import { issueTitle, severityOf } from '../domain/analysis';

/**
 * Уведомления об ошибках — с обоснованием и с действием.
 *
 * Задача требует не просто указать на ошибку, а объяснить, **почему** характеристика
 * некорректна. Поэтому карточка показывает три вещи: что именно не так (`raw`), объяснение из
 * справочника (`message`) и кнопки с допустимыми значениями — ошибка, которую нельзя исправить
 * одним нажатием, стоит пользователю ровно столько же, сколько отсутствие ошибки.
 */
const props = defineProps<{ issues: Issue[] }>();
const emit = defineEmits<{ apply: [from: string, to: string] }>();

const ICONS = { error: OctagonAlert, warning: AlertTriangle, info: Info };

/**
 * Можно ли подставить подсказку в описание одним нажатием.
 *
 * Подставлять есть что только тогда, когда известно, **что** заменить: у «не хватает
 * обязательных характеристик» подсказки — это названия характеристик, а не значения, и
 * дописывать их в описание бессмысленно.
 */
function isReplaceable(issue: Issue): boolean {
  return Boolean(issue.raw) && issue.kind !== 'no_match' && issue.kind !== 'missing_required';
}
</script>

<template>
  <section v-if="props.issues.length" class="panel issues">
    <header class="panel-head">
      <h3>Замечания</h3>
      <span class="muted">{{ props.issues.length }}</span>
    </header>

    <ul class="issue-list">
      <li
        v-for="(issue, index) in props.issues"
        :key="`${issue.kind}-${issue.raw}-${index}`"
        class="issue"
        :class="severityOf(issue.kind)"
      >
        <component :is="ICONS[severityOf(issue.kind)]" :size="17" class="issue-icon" />
        <div class="issue-body">
          <p class="issue-title">
            {{ issueTitle(issue.kind) }}
            <code v-if="issue.raw" class="issue-raw">{{ issue.raw }}</code>
          </p>
          <p class="issue-message">{{ issue.message }}</p>

          <div v-if="issue.suggestions.length" class="issue-actions">
            <button
              v-for="suggestion in issue.suggestions"
              :key="suggestion"
              class="chip"
              :class="{ actionable: isReplaceable(issue) }"
              type="button"
              :disabled="!isReplaceable(issue)"
              @click="emit('apply', issue.raw, suggestion)"
            >
              {{ suggestion }}
            </button>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
