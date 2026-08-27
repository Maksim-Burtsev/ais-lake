import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // ws: /v1/live is a WebSocket; without it the proxy answers the upgrade with a 404.
  server: { port: 5173, proxy: { '/v1': { target: 'http://localhost:8000', ws: true } } },
  // maplibre ships its own web worker; the dep optimizer mangles it in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
});
