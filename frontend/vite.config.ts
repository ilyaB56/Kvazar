import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// API_TARGET — адрес API для dev-прокси: локально http://localhost:8000,
// в docker-режиме http://api:8000 (см. docker-compose / README).
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
