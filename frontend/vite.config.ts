import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
// `defineConfig` взят из `vitest/config`, а не из `vite`: это та же функция, но её тип знает
// про поле `test`. С импортом из `vite` сборка падает на проверке типов, а тройной слэш-
// директивой это не лечится — она действует только в начале файла, до импортов.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // `@/` is the only path alias: a deep relative import across module layers is what makes a
    // boundary violation easy to miss in review.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // The tests use the same aliases and the same plugin set as the build, so a component that
  // compiles in the bundle compiles in a test.
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    globals: true,
  },
  server: {
    port: 5173,
    // In development the SPA and the API are separate origins. Proxying `/api` through Vite keeps
    // them same-origin from the browser's point of view, so the session cookie behaves exactly as
    // it does behind nginx in production and there is no CORS path that only exists locally.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
});
