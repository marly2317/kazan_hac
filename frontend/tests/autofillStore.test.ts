import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AnalyzeParams, AutofillGateway } from '@/modules/autofill/application/ports';
import { useAutofillStore } from '@/modules/autofill/application/autofillStore';
import type { Analysis } from '@/modules/autofill/domain/analysis';

/** Пустой разбор — тестам важно, *что отправили* в шлюз, а не что он вернул. */
function emptyAnalysis(description: string): Analysis {
  return {
    original: description,
    corrected: description,
    textChanged: false,
    state: 'ambiguous',
    resolvedCode: null,
    confidence: 1,
    totalCandidates: 2,
    characteristics: [],
    issues: [],
    steps: [],
    candidates: [],
    nextSteps: [],
  };
}

class RecordingGateway implements AutofillGateway {
  readonly calls: AnalyzeParams[] = [];

  async analyze(params: AnalyzeParams): Promise<Analysis> {
    this.calls.push(params);
    return emptyAnalysis(params.description);
  }

  get lastDescription(): string {
    return this.calls[this.calls.length - 1]?.description ?? '';
  }
}

function makeStore() {
  const store = useAutofillStore();
  const gateway = new RecordingGateway();
  store.useGateway(gateway);
  return { store, gateway };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe('addDetail', () => {
  it('дописывает характеристику в описание и разбирает заново', async () => {
    // Это и есть «поэтапное сужение при вводе характеристик»: нажатие подсказки — такое же
    // редактирование описания, как и ввод руками, поэтому путь к коду один.
    const { store, gateway } = makeStore();
    store.description = 'молоток слесарный';

    store.addDetail('Сталь');
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(store.description).toBe('молоток слесарный Сталь');
    expect(gateway.lastDescription).toBe('молоток слесарный Сталь');
  });

  it('не добавляет то, что уже написано', async () => {
    // Пара нажатий иначе превращает описание в список одинаковых слов.
    const { store, gateway } = makeStore();
    store.description = 'молоток стальной';

    store.addDetail('Сталь');

    expect(store.description).toBe('молоток стальной');
    expect(gateway.calls).toEqual([]);
  });
});

describe('applySuggestion', () => {
  it('заменяет исправленное слово по границам слова', async () => {
    // Без границ «48» заменилось бы и внутри «480».
    const { store, gateway } = makeStore();
    store.description = 'кострукция сварная, 48 и 480';

    store.applySuggestion('48', '50');
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(store.description).toBe('кострукция сварная, 50 и 480');
  });

  it('включает исправление опечаток обратно', async () => {
    const { store } = makeStore();
    store.description = 'молоток';
    store.keepOriginal();
    expect(store.correctTypos).toBe(false);

    store.applySuggestion('молоток', 'молоток слесарный');

    expect(store.correctTypos).toBe(true);
  });
});

describe('keepOriginal', () => {
  it('просит разобрать написанное без исправлений', async () => {
    // Исправление может быть неверным — товара может не быть в справочнике, — и у
    // пользователя должен быть способ сказать «я написал именно это».
    const { store, gateway } = makeStore();
    store.description = 'отвертка крестовая';

    store.keepOriginal();
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(gateway.calls[0]).toEqual({
      description: 'отвертка крестовая',
      correctTypos: false,
      settled: true,
    });
  });
});

describe('analyze', () => {
  it('на пустом описании не ходит в сеть', async () => {
    const { store, gateway } = makeStore();
    store.description = '   ';

    await store.analyze();

    expect(gateway.calls).toEqual([]);
    expect(store.analysis).toBeNull();
  });

  it('сбрасывается вместе с сессией', async () => {
    const { store } = makeStore();
    store.description = 'молоток';
    await store.analyze();
    expect(store.analysis).not.toBeNull();

    store.reset();

    expect(store.description).toBe('');
    expect(store.analysis).toBeNull();
    expect(store.correctTypos).toBe(true);
  });
});


describe('settled', () => {
  it('не помечает законченным то, что ещё набирают', async () => {
    // Разбор идёт по мере ввода, поэтому «куртка зим» — тоже запрос. Пометь его законченным,
    // и в отчёт о нехватке в справочнике попадёт слово «зим».
    const { store, gateway } = makeStore();

    store.type('куртка зим');
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(gateway.calls[0].settled).toBe(false);
  });

  it('помечает законченным явную отправку', async () => {
    // Нажатие кнопки, Enter или подсказки — это «разбирай вот это», то есть описание дописано.
    const { store, gateway } = makeStore();
    store.description = 'куртка зимняя из кожи';

    store.analyzeNow();
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(gateway.calls[0].settled).toBe(true);
  });

  it('помечает законченным добавление характеристики из подсказки', async () => {
    const { store, gateway } = makeStore();
    store.description = 'молоток слесарный';

    store.addDetail('Сталь');
    await vi.waitFor(() => expect(gateway.calls.length).toBe(1));

    expect(gateway.calls[0].settled).toBe(true);
  });
});

class FakeSuggestGateway {
  words: string[] = [];
  calls: string[] = [];

  async suggest(text: string) {
    this.calls.push(text);
    return this.words.map((word) => ({ word, kind: 'value' as const, source: `про ${word}` }));
  }
}

function withSuggestions(words: string[]) {
  const store = useAutofillStore();
  const gateway = new RecordingGateway();
  const suggest = new FakeSuggestGateway();
  suggest.words = words;
  store.useGateway(gateway);
  store.useSuggestGateway(suggest);
  return { store, suggest };
}

describe('подсказки', () => {
  it('подставляются вместо набираемого слова, а не поверх всего текста', async () => {
    // Пользователь уже что-то написал, и подсказка не вправе это стереть.
    const { store } = withSuggestions([]);
    store.description = 'куртка зим';

    store.acceptSuggestion('зимняя');

    expect(store.description).toBe('куртка зимняя ');
  });

  it('после подстановки сразу просят следующее слово', async () => {
    // Как в адресной строке: подставил — и тут же видишь продолжение.
    const { store, suggest } = withSuggestions(['зимняя']);
    store.description = 'кур';

    store.acceptSuggestion('куртка');
    await vi.waitFor(() => expect(suggest.calls).toContain('куртка '));
  });

  it('стрелка вниз выбирает первую подсказку, а не вторую', async () => {
    // Ничего не подсвечено изначально, иначе Enter подставил бы слово наугад вместо отправки.
    const { store } = withSuggestions(['один', 'два']);
    store.description = 'од';
    await store.loadSuggestions();

    expect(store.highlighted).toBe(-1);
    store.moveHighlight(1);
    expect(store.highlighted).toBe(0);
  });

  it('список закольцован и проходит через «ничего не выбрано»', async () => {
    const { store } = withSuggestions(['один', 'два']);
    store.description = 'од';
    await store.loadSuggestions();

    store.moveHighlight(1);
    store.moveHighlight(1);
    expect(store.highlighted).toBe(1);
    store.moveHighlight(1);
    expect(store.highlighted).toBe(-1);
  });

  it('подсказки гаснут при явной отправке', async () => {
    const { store } = withSuggestions(['один']);
    store.description = 'од';
    await store.loadSuggestions();
    expect(store.suggestions.length).toBe(1);

    store.analyzeNow();

    expect(store.suggestions).toEqual([]);
  });

  it('на пустом поле подсказок нет', async () => {
    const { store, suggest } = withSuggestions(['один']);
    store.description = '   ';

    await store.loadSuggestions();

    expect(store.suggestions).toEqual([]);
    expect(suggest.calls).toEqual([]);
  });

  it('отказ подсказок не мешает набору', async () => {
    // Подсказка — удобство; её ошибка не должна ни ломать ввод, ни что-то сообщать.
    const { store } = withSuggestions([]);
    store.useSuggestGateway({
      suggest: async () => {
        throw new Error('сеть отвалилась');
      },
    });
    store.description = 'кур';

    await store.loadSuggestions();

    expect(store.suggestions).toEqual([]);
    expect(store.error).toBe('');
  });
});
