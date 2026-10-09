/**
 * The catalogue, as this application understands it.
 *
 * Domain layer: plain types and rules. The gateway in `infrastructure` translates the API's
 * `snake_case` payload into this shape, so a rename on the server touches one file.
 */

export type Product = {
  id: string;
  sku: string;
  name: string;
  description: string;
  category: string;
  price: string;
  priceLabel: string;
  unit: string;
  inStock: boolean;
};

/** Which stage of the pipeline rewrote the query. Drives the wording of the notice. */
export const CORRECTION_SOURCES = ['none', 'layout', 'dictionary', 'llm'] as const;

export type CorrectionSource = (typeof CORRECTION_SOURCES)[number];

export type Correction = {
  /** What the user typed. */
  original: string;
  /** What was actually searched — this is what goes back into the search box. */
  corrected: string;
  changed: boolean;
  source: CorrectionSource;
};

export type SearchPage = {
  correction: Correction;
  results: Product[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
};

/**
 * Russian plural agreement for a result count: 1 товар, 2 товара, 5 товаров.
 *
 * Russian picks one of three forms by the last digits, so a naïve `count === 1` check reads as
 * broken to a native speaker on every number ending in 2, 3 or 4.
 */
export function productsFound(count: number): string {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return `${count} товаров`;
  if (last === 1) return `${count} товар`;
  if (last >= 2 && last <= 4) return `${count} товара`;
  return `${count} товаров`;
}

/** Why the query was rewritten, for the hint next to the notice. */
const SOURCE_LABELS: Record<CorrectionSource, string> = {
  none: '',
  layout: 'раскладка клавиатуры',
  dictionary: 'опечатка',
  llm: 'ИИ-исправление',
};

export function correctionSourceLabel(source: CorrectionSource): string {
  return SOURCE_LABELS[source] ?? '';
}
