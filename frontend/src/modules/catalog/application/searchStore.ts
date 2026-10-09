import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

import { messageFor } from '@/shared/api/errors';

import type { Correction, Product } from '../domain/product';
import { HttpCatalogGateway } from '../infrastructure/httpCatalogGateway';
import type { CatalogGateway } from './ports';

const PAGE_SIZE = 20;

/**
 * Catalogue search: the application layer of the module.
 *
 * It owns the query, the results and the correction notice. Components read it and call its
 * actions; they never touch the gateway, which keeps `fetch` out of the views.
 */
export const useSearchStore = defineStore('search', () => {
  let gateway: CatalogGateway = new HttpCatalogGateway();

  /** What is in the search box. Replaced by the corrected text once a search comes back. */
  const query = ref('');
  const results = ref<Product[]>([]);
  const correction = ref<Correction | null>(null);
  const total = ref(0);
  const hasMore = ref(false);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error = ref('');
  /** True once a search has returned, so the empty state is not shown before the first one. */
  const searched = ref(false);

  /**
   * The in-flight request.
   *
   * Typing produces overlapping searches, and without this the slower earlier response can land
   * after the faster later one and leave stale results under a newer query.
   */
  let inFlight: AbortController | null = null;

  const isEmpty = computed(() => searched.value && !loading.value && results.value.length === 0);

  function useGateway(next: CatalogGateway): void {
    gateway = next;
  }

  async function search(text: string, options: { correctTypos?: boolean } = {}): Promise<void> {
    inFlight?.abort();
    const controller = new AbortController();
    inFlight = controller;

    query.value = text;
    loading.value = true;
    error.value = '';

    try {
      const page = await gateway.search(
        { query: text, limit: PAGE_SIZE, offset: 0, correctTypos: options.correctTypos },
        controller.signal,
      );

      results.value = page.results;
      correction.value = page.correction;
      total.value = page.total;
      hasMore.value = page.hasMore;
      searched.value = true;

      // The point of the feature: the box now shows what was actually searched, and the notice
      // underneath says where that came from. Done here rather than in the component so the
      // box and the results can never disagree.
      if (page.correction.changed) {
        query.value = page.correction.corrected;
      }
    } catch (cause) {
      if (controller.signal.aborted) return; // superseded by a newer search
      error.value = messageFor(cause, 'Не удалось выполнить поиск.');
      results.value = [];
      total.value = 0;
      hasMore.value = false;
      searched.value = true;
    } finally {
      if (inFlight === controller) {
        inFlight = null;
        loading.value = false;
      }
    }
  }

  /** Re-run the current query with correction switched off. */
  async function searchOriginal(): Promise<void> {
    const original = correction.value?.original;
    if (!original) return;
    await search(original, { correctTypos: false });
  }

  async function loadMore(): Promise<void> {
    if (!hasMore.value || loadingMore.value || loading.value) return;
    loadingMore.value = true;
    try {
      const page = await gateway.search({
        query: query.value,
        limit: PAGE_SIZE,
        offset: results.value.length,
        // The query in the box is already corrected; correcting it again would be a no-op at
        // best and could rewrite it a second time at worst.
        correctTypos: false,
      });
      results.value = [...results.value, ...page.results];
      hasMore.value = page.hasMore;
    } catch (cause) {
      error.value = messageFor(cause, 'Не удалось загрузить ещё товары.');
    } finally {
      loadingMore.value = false;
    }
  }

  /** Clear everything — used when the session ends. */
  function reset(): void {
    inFlight?.abort();
    inFlight = null;
    query.value = '';
    results.value = [];
    correction.value = null;
    total.value = 0;
    hasMore.value = false;
    searched.value = false;
    error.value = '';
  }

  return {
    query,
    results,
    correction,
    total,
    hasMore,
    loading,
    loadingMore,
    error,
    searched,
    isEmpty,
    useGateway,
    search,
    searchOriginal,
    loadMore,
    reset,
  };
});
