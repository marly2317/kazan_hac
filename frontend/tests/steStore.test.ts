import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { SteGateway } from '@/modules/autofill/application/ports';
import { useAutofillStore } from '@/modules/autofill/application/autofillStore';
import type { Result } from '@/modules/autofill/domain/result';
import { UNKNOWN } from '@/modules/autofill/domain/result';

function fakeResult(overrides: Partial<Result> = {}): Result {
  return {
    session_id: 's1',
    stage: 'question',
    message: '',
    text: { original: '', corrected: '', fixes: [] },
    preview: [],
    funnel: [],
    family: null,
    family_candidates: [],
    leaves_count: 2,
    leaves: [],
    question: null,
    final_code: null,
    card: null,
    to_fill_required: [],
    to_fill_optional: [],
    signals: [],
    timings_ms: { total: 1 },
    ...overrides,
  };
}

class FakeGateway implements SteGateway {
  calls: string[] = [];
  async preview() {
    return [];
  }
  async analyze(text: string) {
    this.calls.push(`analyze:${text}`);
    return fakeResult();
  }
  async answer(sessionId: string, charKey: string, value: string) {
    this.calls.push(`answer:${sessionId}:${charKey}:${value}`);
    return fakeResult({ stage: 'done', final_code: 'X-1' });
  }
  async answerText(sessionId: string, charKey: string, text: string) {
    this.calls.push(`text:${sessionId}:${charKey}:${text}`);
    return fakeResult();
  }
  async chooseFamily(sessionId: string, familyId: string) {
    this.calls.push(`family:${familyId}`);
    return fakeResult();
  }
  async chooseLeaf(sessionId: string, code: string) {
    this.calls.push(`leaf:${code}`);
    return fakeResult({ stage: 'done', final_code: code });
  }
  async edit(sessionId: string, charKey: string, value: string) {
    this.calls.push(`edit:${charKey}:${value}`);
    return fakeResult();
  }
}

describe('autofill store', () => {
  let gateway: FakeGateway;
  beforeEach(() => {
    setActivePinia(createPinia());
    gateway = new FakeGateway();
    useAutofillStore().useGateway(gateway);
  });

  it('analyzes the trimmed description and keeps the session', async () => {
    const store = useAutofillStore();
    store.setDescription('  болгарка 125 мм ');
    await store.analyze();
    expect(gateway.calls).toEqual(['analyze:болгарка 125 мм']);
    expect(store.result?.session_id).toBe('s1');
  });

  it('answers within the session and skips with the unknown marker', async () => {
    const store = useAutofillStore();
    store.setDescription('шлифмашина');
    await store.analyze();
    await store.skip('вид');
    await store.answer('вид', 'Углошлифовальная машина');
    expect(gateway.calls).toContain(`answer:s1:вид:${UNKNOWN}`);
    expect(store.result?.final_code).toBe('X-1');
  });

  it('does not call the backend for an empty description', async () => {
    const store = useAutofillStore();
    store.setDescription('   ');
    await store.analyze();
    expect(gateway.calls).toEqual([]);
  });

  it('reset clears the state', async () => {
    const store = useAutofillStore();
    store.setDescription('стол');
    await store.analyze();
    store.reset();
    expect(store.result).toBeNull();
    expect(store.description).toBe('');
  });
});
