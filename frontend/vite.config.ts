import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs so the built app works under any path prefix.
  base: "./",
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
});
