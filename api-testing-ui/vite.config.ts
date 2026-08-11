import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/api-test/',
  plugins: [vue()],
  build: {
    outDir: fileURLToPath(new URL('../api-test', import.meta.url)),
    emptyOutDir: true,
  },
  test: {
    environment: 'node',
  },
})
