<script setup lang="ts">
import { LoaderCircle, PackageSearch } from '@lucide/vue';
import { onMounted } from 'vue';

import { useSearchStore } from '../application/searchStore';
import { productsFound } from '../domain/product';
import CorrectionNotice from './CorrectionNotice.vue';
import ProductCard from './ProductCard.vue';
import SearchBar from './SearchBar.vue';

/** The products tab: a search line, the correction notice, and the results. */
const search = useSearchStore();

onMounted(() => {
  // Open on the full catalogue rather than an empty page, so the tab shows something to search
  // *within* before anything is typed.
  if (!search.searched) void search.search('');
});
</script>

<template>
  <div class="products-page">
    <SearchBar
      :model-value="search.query"
      :busy="search.loading"
      @submit="(q) => search.search(q)"
      @clear="search.search('')"
    />

    <CorrectionNotice :correction="search.correction" @search-original="search.searchOriginal()" />

    <p v-if="search.error" class="error-text" role="alert">{{ search.error }}</p>

    <p v-else-if="!search.loading && search.total > 0" class="result-count muted">
      {{ productsFound(search.total) }}
    </p>

    <div v-if="search.loading" class="results-loader">
      <LoaderCircle class="spin" :size="22" />
      <span>Ищем…</span>
    </div>

    <div v-else-if="search.isEmpty" class="empty-state">
      <PackageSearch :size="40" aria-hidden="true" />
      <h3>Ничего не найдено</h3>
      <p class="muted">Попробуйте изменить запрос или поискать по категории.</p>
    </div>

    <template v-else>
      <div class="product-grid">
        <ProductCard v-for="product in search.results" :key="product.id" :product="product" />
      </div>

      <button
        v-if="search.hasMore"
        class="button secondary load-more"
        type="button"
        :disabled="search.loadingMore"
        @click="search.loadMore()"
      >
        <LoaderCircle v-if="search.loadingMore" class="spin" :size="18" />
        {{ search.loadingMore ? 'Загружаем…' : 'Показать ещё' }}
      </button>
    </template>
  </div>
</template>
