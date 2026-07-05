/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base URL for backend API and WebSocket calls, combining domain and path
   * prefix: "" -> "/api/...", "/prefix" -> "/prefix/api/...",
   * "http://example.com/aaa" -> "http://example.com/aaa/api/...".
   */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
