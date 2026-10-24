import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { expectEveryRequestAnswered } from "./helpers";

// Browser storage holds per-viewer preferences (the collapsed sidebar); each test starts empty.
// A request with no stubbed answer fails the test that made it.
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  expectEveryRequestAnswered();
});
