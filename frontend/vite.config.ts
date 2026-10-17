import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs here in development (`uvicorn src.api.app:create_app --factory`).
const API_TARGET = "http://127.0.0.1:8000";

// Every top-level API path. The dev server forwards them, so the browser sees one site and the
// HttpOnly login cookie (SameSite=strict) is sent. A new top-level API path must be added here.
// Screens live under /app/... so a screen path never equals an API path.
const API_PATHS = [
  "/auth",
  "/me",
  "/uploads",
  "/jobs",
  "/worklist",
  "/overview",
  "/audit",
  "/users",
  "/health",
] as const;

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, { target: API_TARGET }])),
  },
});
