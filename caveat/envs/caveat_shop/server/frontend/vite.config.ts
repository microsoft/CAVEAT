import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8001',
      '/images': 'http://localhost:8001'
    },     
    "host": "0.0.0.0", 
    "allowedHosts": [
      "host.docker.internal"
    ]
  }
})
