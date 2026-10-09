import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // `@/` is the only path alias: a deep relative import across module layers is what makes a
    // boundary violation easy to miss in review.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
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
