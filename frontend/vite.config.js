import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth':  { target: 'http://localhost:8000', changeOrigin: true },
      '/query': { target: 'http://localhost:8000', changeOrigin: true },
      '/admin': { target: 'http://localhost:8000', changeOrigin: true },
      '/health':{ target: 'http://localhost:8000', changeOrigin: true },
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  }
})
