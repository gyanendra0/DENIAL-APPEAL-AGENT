import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Browser storage holds per-viewer preferences (the collapsed sidebar); each test starts empty.
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
