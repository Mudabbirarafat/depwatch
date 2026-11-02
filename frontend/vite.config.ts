import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
// vitest/config re-exports vite's defineConfig with the `test` key typed, so
// the test setup lives beside the build config instead of in a second file.
import { defineConfig } from 'vitest/config'

// The build lands directly in the Python package, because FastAPI serves it:
// one origin, so no CORS, no second deployment to keep alive, and the API and
// the page it drives can never be different versions of each other.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/depwatch/web',
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` proxies to a locally running API, so the dev server and
    // the deployed page talk to the same relative paths.
    proxy: {
      '/analyse': 'http://127.0.0.1:8000',
      '/packages': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      '/gates': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
  },
})
