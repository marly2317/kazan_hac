import { API_ROOT, request } from '@/shared/api/client';

import type { AnalyzeParams, AutofillGateway, SuggestGateway } from '../application/ports';
import type { Analysis, IssueKind, MatchState, Suggestion } from '../domain/analysis';

/** Полезная нагрузка API. Единственное место, где встречаются имена полей бэкенда. */
type AnalysisPayload = {
  original: string;
  corrected: string;
  text_changed: boolean;
  state: MatchState;
  resolved_code: string | null;
  confidence: number;
  total_candidates: number;
  characteristics: {
    name: string;
    raw: string;
    value: string;
    unit: string;
    raw_unit: string;
    was_unified: boolean;
  }[];
  issues: {
    kind: IssueKind;
    characteristic: string;
    raw: string;
    message: string;
    suggestions: string[];
  }[];
  steps: { label: string; detail: string; before: number; after: number; eliminated: number }[];
  candidates: {
    code: string;
    name: string;
    okpd2_code: string;
    score: number;
    support: number;
    distinctions: string[];
  }[];
  next_steps: {
    characteristic: string;
    options: string[];
    splits_into: number;
    is_quantitative: boolean;
    unit: string;
  }[];
};

export class HttpAutofillGateway implements AutofillGateway {
  async analyze(params: AnalyzeParams, signal?: AbortSignal): Promise<Analysis> {
    const query = new URLSearchParams({ description: params.description });
    // Отправляется только при выключении: бэкенд исправляет по умолчанию.
    if (params.correctTypos === false) query.set('correct', '0');
    // И только при включении: бэкенд считает ввод незаконченным по умолчанию.
    if (params.settled) query.set('settled', '1');

    const payload = await request<AnalysisPayload>(
      `${API_ROOT}/autofill/analyze/?${query.toString()}`,
      { signal },
    );

    return {
      original: payload.original,
      corrected: payload.corrected,
      textChanged: payload.text_changed,
      state: payload.state,
      resolvedCode: payload.resolved_code,
      confidence: payload.confidence,
      totalCandidates: payload.total_candidates,
      characteristics: payload.characteristics.map((item) => ({
        name: item.name,
        raw: item.raw,
        value: item.value,
        unit: item.unit,
        rawUnit: item.raw_unit,
        wasUnified: item.was_unified,
      })),
      issues: payload.issues,
      steps: payload.steps,
      candidates: payload.candidates.map((item) => ({
        code: item.code,
        name: item.name,
        okpd2Code: item.okpd2_code,
        score: item.score,
        support: item.support,
        distinctions: item.distinctions,
      })),
      nextSteps: payload.next_steps.map((item) => ({
        characteristic: item.characteristic,
        options: item.options,
        splitsInto: item.splits_into,
        isQuantitative: item.is_quantitative,
        unit: item.unit,
      })),
    };
  }
}


export class HttpSuggestGateway implements SuggestGateway {
  async suggest(text: string, signal?: AbortSignal): Promise<Suggestion[]> {
    const query = new URLSearchParams({ text });
    const payload = await request<{ suggestions: Suggestion[] }>(
      `${API_ROOT}/autofill/suggest/?${query.toString()}`,
      { signal },
    );
    return payload.suggestions;
  }
}
