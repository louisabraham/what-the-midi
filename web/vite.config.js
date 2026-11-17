import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` proxies the API to `what-the-midi serve` (port 8765)
export default defineConfig({
  plugins: [svelte()],
  // the Bravura music font is embedded in VexFlow
  build: { chunkSizeWarningLimit: 1500 },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
});
