/**
 * Ответ API подбора КТРУ (`Result`, архитектура §6.3). Поля — как их отдаёт бэкенд.
 */

export type Stage =
  | 'preview'
  | 'family_selected'
  | 'narrowing'
  | 'question'
  | 'done'
  | 'not_found'
  | 'conflict';

export type Severity = 'error' | 'warning' | 'info';

export type TypoFix = { original: string; corrected: string; reason: string; start: number; end: number };
export type FamilyHit = { family_id: string; short_id: string; name: string; score: number };
export type FunnelStep = { level: string; label: string; count: number };
export type QuestionOption = { label: string; value: string; remaining: number };
export type Question = {
  kind: 'char' | 'family' | 'leaf';
  char_key: string | null;
  title: string;
  why: string;
  options: QuestionOption[];
  allow_free_text: boolean;
};
export type CardCharacteristic = {
  key: string;
  name: string;
  value: string | null;
  values: string[];
  user_value: string | null;
  user_unit: string | null;
  ktru_value: string | null;
  unit: string | null;
  source: 'text' | 'answer' | 'edit' | 'ktru' | 'none';
  quote: string | null;
  required: boolean;
  kind: number;
  confidence: string | null;
  unified: boolean;
  action: string | null;
  options: string[];
};
export type Card = {
  ktru_code: string | null;
  ktru_name: string;
  okpd2: { code: string; name: string };
  characteristics: CardCharacteristic[];
  extra: { name: string; value: string; unit: string | null; quote?: string; matched_from?: string | null }[];
  brand: string | null;
  model: string | null;
};
export type Signal = {
  type: string;
  severity: Severity;
  message: string;
  char: string | null;
  char_key: string | null;
  user_value: string | null;
  quote: string | null;
  allowed: string[];
  nearest_allowed: string | null;
  suggestion: string | null;
};
export type FillItem = { key: string; name: string; options: string[] };

export type Result = {
  session_id: string;
  stage: Stage;
  message: string;
  text: { original: string; corrected: string; fixes: TypoFix[] };
  preview: FamilyHit[];
  funnel: FunnelStep[];
  family: { id: string; name: string; okpd2: string; okpd2_name: string; category: string; reason: string; score: number } | null;
  family_candidates: { id: string; name: string; score: number; reason: string }[];
  leaves_count: number;
  leaves: { code: string; name: string; score: number; distinct: { char: string; value: string }[] }[];
  question: Question | null;
  final_code: string | null;
  card: Card | null;
  to_fill_required: FillItem[];
  to_fill_optional: FillItem[];
  signals: Signal[];
  timings_ms: Record<string, number>;
  trace?: {
    family_by: string;
    llm1_called: boolean;
    llm2_called: boolean;
    reranker: boolean;
    reranker_ms?: number | null;
    llm_configured: boolean;
    llm_error: string | null;
    steps?: { step: string; model: string | null; ms: number; note: string }[];
  };
};

export const UNKNOWN = '__unknown__';

const STAGE_LABELS: Record<Stage, string> = {
  preview: 'Предпросмотр',
  family_selected: 'Тип товара определён',
  narrowing: 'Сужение кодов',
  question: 'Нужно уточнение',
  done: 'Код КТРУ определён',
  not_found: 'Не найдено в КТРУ',
  conflict: 'Противоречие в значениях',
};

export function stageLabel(stage: Stage): string {
  return STAGE_LABELS[stage] ?? stage;
}

const SOURCE_LABELS: Record<CardCharacteristic['source'], string> = {
  text: 'из текста',
  answer: 'ответ',
  edit: 'правка',
  ktru: 'по КТРУ',
  none: '—',
};

export function sourceLabel(source: CardCharacteristic['source']): string {
  return SOURCE_LABELS[source] ?? source;
}
