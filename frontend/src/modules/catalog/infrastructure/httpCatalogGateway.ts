import { API_ROOT, request } from '@/shared/api/client';

import type { CatalogGateway, SearchParams } from '../application/ports';
import type { CorrectionSource, Product, SearchPage } from '../domain/product';

/** The API's payloads. The only place the backend's field names appear. */
type ProductPayload = {
  id: string;
  sku: string;
  name: string;
  description: string;
  category: string;
  price: string;
  price_label: string;
  unit: string;
  in_stock: boolean;
};

type SearchPayload = {
  correction: {
    original: string;
    corrected: string;
    changed: boolean;
    source: CorrectionSource;
  };
  results: ProductPayload[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

function toProduct(payload: ProductPayload): Product {
  return {
    id: payload.id,
    sku: payload.sku,
    name: payload.name,
    description: payload.description,
    category: payload.category,
    price: payload.price,
    priceLabel: payload.price_label,
    unit: payload.unit,
    inStock: payload.in_stock,
  };
}

export class HttpCatalogGateway implements CatalogGateway {
  async search(params: SearchParams, signal?: AbortSignal): Promise<SearchPage> {
    const query = new URLSearchParams({ q: params.query });
    if (params.limit !== undefined) query.set('limit', String(params.limit));
    if (params.offset !== undefined) query.set('offset', String(params.offset));
    // Only sent when switched off: the backend defaults to correcting.
    if (params.correctTypos === false) query.set('correct', '0');

    const payload = await request<SearchPayload>(
      `${API_ROOT}/catalog/products/?${query.toString()}`,
      { signal },
    );

    return {
      correction: payload.correction,
      results: payload.results.map(toProduct),
      total: payload.total,
      limit: payload.limit,
      offset: payload.offset,
      hasMore: payload.has_more,
    };
  }
}
