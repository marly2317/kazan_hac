import type { SearchPage } from '../domain/product';

export type SearchParams = {
  query: string;
  limit?: number;
  offset?: number;
  /**
   * `false` is what the "search for the original instead" link sends: the user has seen the
   * correction and rejected it, so correcting again would undo their choice.
   */
  correctTypos?: boolean;
};

/** The port the search store depends on. The HTTP implementation lives in `infrastructure`. */
export interface CatalogGateway {
  search(params: SearchParams, signal?: AbortSignal): Promise<SearchPage>;
}
