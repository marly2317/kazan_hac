<script setup lang="ts">
import { BadgeCheck, Copy } from '@lucide/vue';
import { ref } from 'vue';

import type { Candidate, MatchState } from '../domain/analysis';

/**
 * Позиции КТРУ, которые ещё подходят.
 *
 * Главное здесь — `distinctions`. КТРУ даёт разным позициям одинаковые наименования: в
 * справочнике три позиции «Куртка зимняя из кожи» и двенадцать «Молоток». Список одинаковых
 * имён — это не выбор, который пользователь может сделать, поэтому под каждой показано то, чем
 * она отличается от остальных.
 */
const props = defineProps<{ candidates: Candidate[]; state: MatchState; total: number }>();

const copied = ref('');

async function copy(code: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(code);
    copied.value = code;
    setTimeout(() => {
      if (copied.value === code) copied.value = '';
    }, 1500);
  } catch {
    // Буфер обмена может быть недоступен (нет разрешения, не защищённый контекст). Код виден
    // на экране и выделяется мышью, так что терять тут нечего.
  }
}
</script>

<template>
  <section v-if="props.candidates.length" class="panel">
    <header class="panel-head">
      <h3>Позиции КТРУ</h3>
      <span class="muted">
        показано {{ props.candidates.length }} из {{ props.total }}
      </span>
    </header>

    <ul class="candidates">
      <li
        v-for="(candidate, index) in props.candidates"
        :key="candidate.code"
        class="candidate"
        :class="{ leader: index === 0 && props.state === 'resolved' }"
      >
        <div class="candidate-head">
          <BadgeCheck
            v-if="index === 0 && props.state === 'resolved'"
            :size="18"
            class="candidate-check"
          />
          <div class="candidate-title">
            <p class="candidate-name">{{ candidate.name }}</p>
            <p class="candidate-code">
              <code>{{ candidate.code }}</code>
              <span class="muted">ОКПД2 {{ candidate.okpd2Code }}</span>
            </p>
          </div>
          <button class="text-button copy" type="button" @click="copy(candidate.code)">
            <Copy :size="15" />
            <span>{{ copied === candidate.code ? 'Скопирован' : 'Копировать' }}</span>
          </button>
        </div>

        <ul v-if="candidate.distinctions.length" class="distinctions">
          <li v-for="line in candidate.distinctions" :key="line">{{ line }}</li>
        </ul>

        <p v-if="candidate.support" class="candidate-support muted">
          подтверждено характеристик из описания: {{ candidate.support }}
        </p>
      </li>
    </ul>
  </section>
</template>
