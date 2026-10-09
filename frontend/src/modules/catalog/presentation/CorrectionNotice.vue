<script setup lang="ts">
import { computed } from 'vue';

import { type Correction, correctionSourceLabel } from '../domain/product';

/**
 * The "showing results for …" line, the way a browser's search does it.
 *
 * Two facts, in the order that matters: what was actually searched, then what the user typed —
 * with the original one click away. The search was already *run* on the corrected text, so the
 * notice reports a decision rather than offering one; taking the original back is the escape
 * hatch, not the default.
 */
const props = defineProps<{ correction: Correction | null }>();
const emit = defineEmits<{ searchOriginal: [] }>();

const visible = computed(() => Boolean(props.correction?.changed));
const sourceLabel = computed(() =>
  props.correction ? correctionSourceLabel(props.correction.source) : '',
);
</script>

<template>
  <!-- `role="status"`: a screen reader should hear that the query was changed without the
       announcement stealing focus from the input the user is still typing in. -->
  <p v-if="visible && props.correction" class="correction-notice" role="status">
    <span>
      Показаны результаты по запросу
      <strong>{{ props.correction.corrected }}</strong>
    </span>
    <span class="correction-original">
      Искать вместо этого
      <button type="button" class="link-button" @click="emit('searchOriginal')">
        {{ props.correction.original }}
      </button>
      <small v-if="sourceLabel" class="correction-source">· {{ sourceLabel }}</small>
    </span>
  </p>
</template>
