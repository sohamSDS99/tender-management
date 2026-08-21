import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The backend is reached through relative URLs: proxied here in development and
// by nginx in the Docker image, so no CORS handling is needed in the browser.
const target = process.env.VITE_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/health': { target, changeOrigin: true },
    },
  },
  preview: {
    port: 4173,
    host: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/health': { target, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
