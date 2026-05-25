import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const repoRoot = path.resolve(__dirname, '..')

export default defineConfig(({ mode }) => {
  const repoEnv = loadEnv(mode, repoRoot, '')
  const localEnv = loadEnv(mode, process.cwd(), '')
  const env = { ...repoEnv, ...localEnv }
  const target = env.VITE_API_URL || 'http://127.0.0.1:8001'

  return {
    envDir: repoRoot,
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
        },
        '/auth': {
          target,
          changeOrigin: true,
        },
        '/ws': {
          target,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    build: {
      chunkSizeWarningLimit: 750,
      rollupOptions: {
        output: {
          // Split vendor libraries into stable, cacheable chunks
          manualChunks(id) {
            if (id.includes('node_modules')) {
              // Heavy graph/layout libs get their own chunk
              if (id.includes('dagre') || id.includes('@dagrejs')) return 'vendor-dagre'
              if (id.includes('@xyflow') || id.includes('reactflow')) return 'vendor-reactflow'
              // Search lib
              if (id.includes('minisearch')) return 'vendor-search'
              // React core
              if (id.includes('react-dom') || id.includes('react/')) return 'vendor-react'
              // Everything else in node_modules
              return 'vendor'
            }
          },
        },
      },
    },
  }
})
