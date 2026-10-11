import { defineStore } from 'pinia';
import { ref } from 'vue';

import { messageFor } from '@/shared/api/errors';

import type { FamilyHit, Result } from '../domain/result';
import { UNKNOWN } from '../domain/result';
import { HttpSteGateway } from '../infrastructure/httpSteGateway';
import type { SteGateway } from './ports';

/** Пауза перед предпросмотром после последнего нажатия, мс: поиск без LLM, десятки мс. */
const PREVIEW_DEBOUNCE_MS = 300;
const PREVIEW_MIN_CHARS = 3;

/**
 * Подбор кода КТРУ: прикладной слой экрана.
 *
 * Пока пользователь печатает — предпросмотр семейств (без LLM). По кнопке — полный разбор
 * (до двух вызовов LLM), дальше сессия: ответ на вопрос кнопкой пересчитывается на сервере без
 * LLM, ответ текстом разбирает мини-LLM.
 */
export const useAutofillStore = defineStore('autofill', () => {
  let gateway: SteGateway = new HttpSteGateway();

  const description = ref('');
  const preview = ref<FamilyHit[]>([]);
  const result = ref<Result | null>(null);
  const loading = ref(false);
  const error = ref('');

  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  let previewAbort: AbortController | undefined;

  function useGateway(next: SteGateway): void {
    gateway = next;
  }

  function setDescription(text: string): void {
    description.value = text;
    clearTimeout(previewTimer);
    if (text.trim().length < PREVIEW_MIN_CHARS) {
      preview.value = [];
      return;
    }
    previewTimer = setTimeout(() => void loadPreview(text), PREVIEW_DEBOUNCE_MS);
  }

  async function loadPreview(text: string): Promise<void> {
    previewAbort?.abort();
    previewAbort = new AbortController();
    try {
      preview.value = await gateway.preview(text, previewAbort.signal);
    } catch {
      // Предпросмотр — подсказка, а не результат: его ошибка не мешает разбору.
    }
  }

  async function run(action: () => Promise<Result>): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      result.value = await action();
    } catch (cause) {
      error.value = messageFor(cause);
    } finally {
      loading.value = false;
    }
  }

  function sessionId(): string {
    return result.value?.session_id ?? '';
  }

  const analyze = () => {
    const text = description.value.trim();
    if (!text) return Promise.resolve();
    return run(() => gateway.analyze(text));
  };
  const answer = (charKey: string, value: string) =>
    run(() => gateway.answer(sessionId(), charKey, value));
  const skip = (charKey: string) => answer(charKey, UNKNOWN);
  const answerText = (charKey: string, text: string) =>
    run(() => gateway.answerText(sessionId(), charKey, text));
  const chooseFamily = (familyId: string) =>
    run(() => gateway.chooseFamily(sessionId(), familyId));
  const chooseLeaf = (code: string) => run(() => gateway.chooseLeaf(sessionId(), code));
  const edit = (charKey: string, value: string) =>
    run(() => gateway.edit(sessionId(), charKey, value));
  const setExtra = (name: string, value: string) =>
    run(() => gateway.setExtra(sessionId(), name, value));

  function reset(): void {
    clearTimeout(previewTimer);
    previewAbort?.abort();
    description.value = '';
    preview.value = [];
    result.value = null;
    error.value = '';
    loading.value = false;
  }

  return {
    description,
    preview,
    result,
    loading,
    error,
    useGateway,
    setDescription,
    analyze,
    answer,
    skip,
    answerText,
    chooseFamily,
    chooseLeaf,
    edit,
    setExtra,
    reset,
  };
});
