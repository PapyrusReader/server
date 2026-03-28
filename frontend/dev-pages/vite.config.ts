import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

const rootDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: rootDir,
  publicDir: false,
  css: {
    devSourcemap: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        authSandbox: resolve(rootDir, "src/pages/auth-sandbox/main.ts"),
        powersyncSandbox: resolve(rootDir, "src/pages/powersync-sandbox/main.ts"),
      },
    },
  },
});
