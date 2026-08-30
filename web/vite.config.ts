import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // ws: /v1/live is a WebSocket; without it the proxy answers the upgrade with a 404.
  // /ship/{slug}-{mmsi} is server-rendered (api/app/ssr.py) and then taken over
  // by this bundle, so in dev the page must come from the api, not from Vite.
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', ws: true },
      '/ship': { target: 'http://localhost:8000' },
    },
  },
  // ssr.py reads .vite/manifest.json to emit the hashed script and css tags.
  build: { manifest: true },
  // maplibre ships its own web worker; the dep optimizer mangles it in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
});
