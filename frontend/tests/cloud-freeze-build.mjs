import { build } from 'vite'

// Always called in an isolated source snapshot; never load a checkout .env.
await build({
  root: process.cwd(), configFile: process.cwd() + '/vite.config.ts',
  configLoader: 'native', envDir: false, cacheDir: process.cwd() + '/.vite-cache',
  build: { outDir: 'cloud-freeze-dist', emptyOutDir: false,
    rollupOptions: { input: process.cwd() + '/tests/cloud-freeze-harness.html' } },
})
