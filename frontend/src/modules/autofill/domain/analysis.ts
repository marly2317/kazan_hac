/**
 * Автозаполнение СТЕ, как его понимает интерфейс.
 *
 * Доменный слой: типы и правила над ними, без `fetch` и без Vue. Шлюз в `infrastructure`
 * переводит `snake_case` ответа API в эти типы, поэтому переименование поля на сервере
 * затрагивает один файл.
 */

/** Как далеко продвинулось сужение. */
export const MATCH_STATES = ['resolved', 'ambiguous', 'not_found', 'empty'] as const;

export type MatchState = (typeof MATCH_STATES)[number];

/** Почему фрагмент описания некорректен. */
export const ISSUE_KINDS = [
  'typo',
  'normalized',
  'unknown_value',
  'out_of_range',
  'wrong_unit',
  'unknown_unit',
  'missing_required',
  'no_match',
] as const;

export type IssueKind = (typeof ISSUE_KINDS)[number];

export type ExtractedCharacteristic = {
  name: string;
  /** Что написал пользователь — «500 г». */
  raw: string;
  /** Значение после унификации — «0.5». */
  value: string;
  /** Единица ОКЕИ после унификации — «Килограмм». */
  unit: string;
  /** Единица, как её написали — «г». */
  rawUnit: string;
  /** Отличается ли унифицированная форма от написанной. */
  wasUnified: boolean;
};

export type Issue = {
  kind: IssueKind;
  characteristic: string;
  raw: string;
  message: string;
  suggestions: string[];
};

export type NarrowingStep = {
  label: string;
  detail: string;
  before: number;
  after: number;
  eliminated: number;
};

export type Candidate = {
  code: string;
  name: string;
  okpd2Code: string;
  score: number;
  support: number;
  /** Чем эта позиция отличается от остальных — КТРУ даёт разным позициям одинаковые имена. */
  distinctions: string[];
};

export type NextStep = {
  characteristic: string;
  options: string[];
  splitsInto: number;
  /** Ожидается число с единицей, а не одно из `options` дословно. */
  isQuantitative: boolean;
  /** Единица ОКЕИ, в которой ждут ответ, — для количественной характеристики. */
  unit: string;
};

export type Analysis = {
  original: string;
  corrected: string;
  /** Отличается ли разобранный текст от написанного больше, чем регистром букв. */
  textChanged: boolean;
  state: MatchState;
  resolvedCode: string | null;
  confidence: number;
  totalCandidates: number;
  characteristics: ExtractedCharacteristic[];
  issues: Issue[];
  steps: NarrowingStep[];
  candidates: Candidate[];
  nextSteps: NextStep[];
};

/**
 * Насколько серьёзна проблема. Определяет цвет и порядок в списке.
 *
 * `normalized` — не ошибка вовсе: это слово пользователя, приведённое к форме справочника.
 * Показывать его тем же красным, что «значение вне диапазона», значит обвинять в ошибке
 * правильный ввод — ровно то, за что снижают балл за качество исправлений.
 */
export type Severity = 'error' | 'warning' | 'info';

const SEVERITIES: Record<IssueKind, Severity> = {
  no_match: 'error',
  out_of_range: 'error',
  unknown_value: 'error',
  wrong_unit: 'error',
  unknown_unit: 'warning',
  missing_required: 'warning',
  typo: 'warning',
  normalized: 'info',
};

export function severityOf(kind: IssueKind): Severity {
  return SEVERITIES[kind] ?? 'warning';
}

/** Человеческое название вида проблемы — заголовок карточки уведомления. */
const ISSUE_TITLES: Record<IssueKind, string> = {
  typo: 'Опечатка',
  normalized: 'Приведено к справочнику',
  unknown_value: 'Недопустимое значение',
  out_of_range: 'Значение вне диапазона',
  wrong_unit: 'Неверная единица измерения',
  unknown_unit: 'Единица не из ОКЕИ',
  missing_required: 'Не хватает характеристик',
  no_match: 'Позиция не определена',
};

export function issueTitle(kind: IssueKind): string {
  return ISSUE_TITLES[kind] ?? 'Замечание';
}

const STATE_LABELS: Record<MatchState, string> = {
  resolved: 'Код КТРУ определён',
  ambiguous: 'Нужно уточнить',
  not_found: 'Подходящей позиции не нашлось',
  empty: 'Введите описание товара',
};

export function stateLabel(state: MatchState): string {
  return STATE_LABELS[state] ?? '';
}

/**
 * Согласование числительного с «позиция»: 1 позиция, 2 позиции, 5 позиций.
 *
 * В русском форма выбирается по последним цифрам, и проверка `count === 1` читается носителем
 * как поломка на каждом числе, кончающемся на 2, 3 или 4.
 */
export function positionsLeft(count: number): string {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return `${count} позиций`;
  if (last === 1) return `${count} позиция`;
  if (last >= 2 && last <= 4) return `${count} позиции`;
  return `${count} позиций`;
}

/**
 * Сортировка проблем: сначала то, что мешает получить код.
 *
 * Пользователь читает сверху вниз и останавливается, когда понял, что делать. Если первым
 * стоит «приведено к справочнику», он до «значение вне диапазона» может не дойти.
 */
const ORDER: Record<Severity, number> = { error: 0, warning: 1, info: 2 };

export function byUrgency(left: Issue, right: Issue): number {
  return ORDER[severityOf(left.kind)] - ORDER[severityOf(right.kind)];
}

/** Откуда взята подсказка — определяет подпись рядом с ней. */
export const SUGGESTION_KINDS = ['position', 'characteristic', 'value'] as const;

export type SuggestionKind = (typeof SUGGESTION_KINDS)[number];

export type Suggestion = {
  /** Слово, которое подставится вместо набираемого. */
  word: string;
  kind: SuggestionKind;
  /** Фраза справочника, откуда слово взято, — показывается как пояснение. */
  source: string;
};

const SUGGESTION_LABELS: Record<SuggestionKind, string> = {
  position: 'позиция',
  characteristic: 'характеристика',
  value: 'значение',
};

export function suggestionLabel(kind: SuggestionKind): string {
  return SUGGESTION_LABELS[kind] ?? '';
}

/**
 * Заменить набираемое (последнее) слово на выбранное и поставить пробел.
 *
 * Именно последнее слово, а не весь текст: пользователь уже что-то написал, и подсказка не
 * вправе это стереть. Пробел в конце — чтобы следом сразу пришла подсказка следующего слова,
 * как в адресной строке.
 */
export function applyCompletion(text: string, word: string): string {
  if (!text || /\s$/.test(text)) return `${text}${word} `;
  const head = text.replace(/\S+$/, '');
  return `${head}${word} `;
}
