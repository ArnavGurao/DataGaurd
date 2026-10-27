import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'deployment-mode',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'deployment-mode.json',
          source: JSON.stringify({ mode: process.env.VITE_DATA_MODE || 'demo' }),
        });
      },
    },
  ],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
});
