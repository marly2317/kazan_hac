<script setup lang="ts">
import type { Product } from '../domain/product';

const props = defineProps<{ product: Product }>();
</script>

<template>
  <article class="product-card">
    <header class="product-head">
      <h3>{{ props.product.name }}</h3>
      <span class="product-sku">{{ props.product.sku }}</span>
    </header>

    <p v-if="props.product.description" class="product-description">
      {{ props.product.description }}
    </p>

    <footer class="product-foot">
      <span class="product-category">{{ props.product.category }}</span>
      <span class="product-price">
        {{ props.product.priceLabel }}
        <small class="muted">/ {{ props.product.unit }}</small>
      </span>
      <span class="stock" :class="{ out: !props.product.inStock }">
        {{ props.product.inStock ? 'В наличии' : 'Под заказ' }}
      </span>
    </footer>
  </article>
</template>
