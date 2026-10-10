<script setup lang="ts">
import { ArrowRight, Check } from '@lucide/vue';

import type { ExtractedCharacteristic } from '../domain/analysis';

/**
 * Что сервис извлёк из описания и что из этого было унифицировано.
 *
 * Показывается **и** то, что написал пользователь, **и** то, во что это превратилось: задача
 * требует, чтобы в интерфейсе было видно, какие значения и единицы были сопоставлены. Таблица,
 * показывающая только результат, выглядит так, будто пользователь сам написал «0.5 Килограмм».
 */
defineProps<{ items: ExtractedCharacteristic[] }>();
</script>

<template>
  <section v-if="items.length" class="panel">
    <header class="panel-head">
      <h3>Характеристики</h3>
      <span class="muted">{{ items.length }} извлечено</span>
    </header>

    <table class="characteristics">
      <thead>
        <tr>
          <th>Характеристика</th>
          <th>Как написано</th>
          <th>Унифицировано</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.name">
          <td class="name">{{ item.name }}</td>
          <td class="raw">{{ item.raw }}</td>
          <td class="unified">
            <span v-if="item.wasUnified" class="unified-pair">
              <ArrowRight :size="14" aria-hidden="true" />
              <strong>{{ item.value }}</strong>
              <span v-if="item.unit" class="unit">{{ item.unit }}</span>
            </span>
            <span v-else class="as-is muted">
              <Check :size="14" aria-hidden="true" />
              без изменений
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
