// This build is run with the same offline guard as the production build.
import { build } from 'vite'

await build({
  root: process.cwd(),
  configFile: process.cwd() + '/vite.config.ts',
  configLoader: 'native',
  envDir: false,
  cacheDir: process.cwd() + '/.vite-cache',
  build: {
    outDir: process.cwd() + '/lifecycle-dist',
    emptyOutDir: false,
    rollupOptions: { input: process.cwd() + '/tests/lifecycle-harness.html' },
  },
})
