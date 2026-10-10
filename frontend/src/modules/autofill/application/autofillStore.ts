import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

import { messageFor } from '@/shared/api/errors';

import type { Analysis, Suggestion } from '../domain/analysis';
import { applyCompletion, byUrgency } from '../domain/analysis';
import {
  HttpAutofillGateway,
  HttpSuggestGateway,
} from '../infrastructure/httpAutofillGateway';
import type { AutofillGateway, SuggestGateway } from './ports';

/**
 * Задержка перед разбором после последнего нажатия клавиши, мс.
 *
 * Задача требует показывать подбор кода «в режиме реального времени», то есть разбирать по мере
 * ввода, а не по кнопке. Но запрос на каждый символ — это запрос на каждый символ: бэкенд
 * считает тригаммное сходство по всему справочнику. 350 мс — пауза, которую человек делает,
 * дописав слово, и она же отсекает промежуточные состояния вроде «курт», «куртк».
 */
const DEBOUNCE_MS = 350;

/**
 * Задержка перед запросом подсказок, мс.
 *
 * Намного короче, чем перед разбором: подсказка обязана успевать за набором, иначе она
 * приходит к слову, которое пользователь уже дописал. Позволить себе это можно потому, что
 * словарь подсказок лежит в памяти процесса и ответ занимает 3–5 мс против 39 мс у разбора.
 */
const SUGGEST_DEBOUNCE_MS = 90;

/**
 * Автозаполнение СТЕ: прикладной слой модуля.
 *
 * Единственный источник истины — **текст описания**. Всё, что делает пользователь — принимает
 * исправление, добавляет характеристику из подсказки, правит значение — редактирует этот текст,
 * после чего описание разбирается заново. Поэтому «поэтапное сужение по мере ввода
 * характеристик» работает одинаково, печатает ли пользователь руками или нажимает подсказку, а
 * у состояния нет второй, расходящейся с текстом копии. API при этом остаётся без состояния:
 * один `GET`, который можно повторить и которым можно поделиться ссылкой.
 */
export const useAutofillStore = defineStore('autofill', () => {
  let gateway: AutofillGateway = new HttpAutofillGateway();
  let suggestGateway: SuggestGateway = new HttpSuggestGateway();

  /** Что в поле описания. */
  const description = ref('');
  const analysis = ref<Analysis | null>(null);
  const loading = ref(false);
  const error = ref('');
  /** Исправление опечаток включено. Выключается, когда пользователь отклонил исправление. */
  const correctTypos = ref(true);
  /** True после первого ответа — чтобы пустое состояние не мелькало до него. */
  const analysed = ref(false);

  /** Подсказки под полем и то, что выбрано клавишами. -1 — не выбрано ничего. */
  const suggestions = ref<Suggestion[]>([]);
  const highlighted = ref(-1);

  /**
   * Запрос в полёте.
   *
   * Ввод порождает перекрывающиеся запросы, и без этого более медленный ранний ответ может
   * прийти после быстрого позднего и оставить устаревший разбор под новым описанием.
   */
  let inFlight: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let suggestInFlight: AbortController | null = null;
  let suggestTimer: ReturnType<typeof setTimeout> | null = null;

  const issues = computed(() => [...(analysis.value?.issues ?? [])].sort(byUrgency));
  const isEmpty = computed(() => description.value.trim().length === 0);

  function useGateway(next: AutofillGateway): void {
    gateway = next;
  }

  function useSuggestGateway(next: SuggestGateway): void {
    suggestGateway = next;
  }

  /** Спрятать подсказки — после выбора, по Esc и после разбора. */
  function clearSuggestions(): void {
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestInFlight?.abort();
    suggestions.value = [];
    highlighted.value = -1;
  }

  async function loadSuggestions(): Promise<void> {
    const text = description.value;
    suggestInFlight?.abort();
    if (!text.trim()) {
      suggestions.value = [];
      highlighted.value = -1;
      return;
    }

    const controller = new AbortController();
    suggestInFlight = controller;
    try {
      const found = await suggestGateway.suggest(text, controller.signal);
      if (controller.signal.aborted) return;
      suggestions.value = found;
      // Ничего не подсвечено: первое нажатие стрелки выбирает первую подсказку, и Enter без
      // стрелок по-прежнему отправляет описание, а не подставляет слово наугад.
      highlighted.value = -1;
    } catch {
      // Подсказка — удобство. Её отказ не должен ни мешать набору, ни что-то сообщать.
      if (!controller.signal.aborted) suggestions.value = [];
    } finally {
      if (suggestInFlight === controller) suggestInFlight = null;
    }
  }

  /**
   * Перемещение по списку клавишами. Закольцовано, как в адресной строке.
   *
   * Состояний на одно больше, чем подсказок: -1 — «ничего не выбрано», и цикл проходит через
   * него. Поэтому арифметика ведётся в сдвинутом диапазоне `0…count`, где 0 — это «ничего»,
   * а в конце сдвиг снимается. Второе взятие остатка — из-за того, что `%` в JavaScript для
   * отрицательных чисел возвращает отрицательное, и шаг вверх с первой позиции иначе уводил
   * бы в никуда.
   */
  function moveHighlight(step: number): void {
    const count = suggestions.value.length;
    if (!count) return;
    const slots = count + 1;
    const next = (((highlighted.value + 1 + step) % slots) + slots) % slots;
    highlighted.value = next - 1;
  }

  /** Подставить подсказку вместо набираемого слова. */
  function acceptSuggestion(word: string): void {
    description.value = applyCompletion(description.value, word);
    clearSuggestions();
    // Слово дописано — сразу просим продолжение, как делает адресная строка.
    void loadSuggestions();
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void analyze(), DEBOUNCE_MS);
  }

  /** Подставить то, что подсвечено. `false`, если выбирать нечего. */
  function acceptHighlighted(): boolean {
    const chosen = suggestions.value[highlighted.value];
    if (!chosen) return false;
    acceptSuggestion(chosen.word);
    return true;
  }

  async function analyze(settled = false): Promise<void> {
    const text = description.value.trim();
    inFlight?.abort();

    if (!text) {
      analysis.value = null;
      analysed.value = false;
      error.value = '';
      loading.value = false;
      return;
    }

    const controller = new AbortController();
    inFlight = controller;
    loading.value = true;
    error.value = '';

    try {
      const result = await gateway.analyze(
        { description: text, correctTypos: correctTypos.value, settled },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      analysis.value = result;
      analysed.value = true;
    } catch (cause) {
      if (controller.signal.aborted) return;
      error.value = messageFor(cause);
    } finally {
      if (inFlight === controller) {
        loading.value = false;
        inFlight = null;
      }
    }
  }

  /**
   * Напечатанный символ: разобрать, когда пользователь остановился.
   *
   * `settled` не ставится: пауза в наборе — это пауза, а не законченная мысль, и «куртка зим»
   * не должно попасть в отчёт о нехватке в справочнике как слово «зим».
   */
  function type(text: string): void {
    description.value = text;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void analyze(), DEBOUNCE_MS);
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => void loadSuggestions(), SUGGEST_DEBOUNCE_MS);
  }

  /**
   * Разобрать немедленно — нажатие кнопки, Enter или подсказки ждать не должно.
   *
   * Это же и признак законченного описания: пользователь явно сказал «разбирай вот это».
   */
  function analyzeNow(): void {
    if (timer) clearTimeout(timer);
    clearSuggestions();
    void analyze(true);
  }

  /**
   * Дописать характеристику в описание.
   *
   * Так работает подсказка «добавьте: Тип каркаса → Металлический»: значение дописывается в
   * текст, и сужение идёт от нового описания. Повторное добавление того же значения
   * игнорируется, иначе пара нажатий превращает описание в список одинаковых слов.
   */
  function addDetail(value: string): void {
    const text = description.value.trim();
    if (text.toLowerCase().includes(value.toLowerCase())) return;
    description.value = text ? `${text} ${value}` : value;
    analyzeNow();
  }

  /** Принять исправление: подставить в текст то, что предложил сервис. */
  function applySuggestion(from: string, to: string): void {
    if (!from) {
      description.value = to;
    } else {
      // Границы слова, иначе «48» внутри «480» тоже заменится.
      const pattern = new RegExp(`(?<![0-9a-zа-яё])${escapeRegExp(from)}(?![0-9a-zа-яё])`, 'giu');
      description.value = description.value.replace(pattern, to);
    }
    correctTypos.value = true;
    analyzeNow();
  }

  /**
   * Отклонить исправления и разобрать то, что было написано.
   *
   * Исправление может быть неверным — например, если товара нет в справочнике, — и у
   * пользователя должен быть способ сказать «я написал именно это».
   */
  function keepOriginal(): void {
    correctTypos.value = false;
    analyzeNow();
  }

  function reset(): void {
    inFlight?.abort();
    if (timer) clearTimeout(timer);
    clearSuggestions();
    description.value = '';
    analysis.value = null;
    error.value = '';
    loading.value = false;
    analysed.value = false;
    correctTypos.value = true;
  }

  return {
    description,
    suggestions,
    highlighted,
    analysis,
    issues,
    loading,
    error,
    analysed,
    correctTypos,
    isEmpty,
    analyze,
    analyzeNow,
    type,
    addDetail,
    applySuggestion,
    keepOriginal,
    reset,
    loadSuggestions,
    clearSuggestions,
    moveHighlight,
    acceptSuggestion,
    acceptHighlighted,
    useGateway,
    useSuggestGateway,
  };
});

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
