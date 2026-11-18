import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` proxies the API to `what-the-midi serve` (port 8765)
export default defineConfig({
  plugins: [svelte()],
  // the Bravura music font is embedded in VexFlow
  // static.html: the search with no server (whatthemidi/static.py)
  build: { chunkSizeWarningLimit: 1500, rollupOptions: { input: ['index.html', 'static.html'] } },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
});
