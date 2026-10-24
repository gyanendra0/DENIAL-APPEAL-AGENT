import { afterEach, describe, expect, it, vi } from "vitest";

import { readSidebarCollapsed, writeSidebarCollapsed } from "../../src/screens/sidebarPreference";

describe("sidebarPreference", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts expanded and remembers a collapse", () => {
    expect(readSidebarCollapsed()).toBe(false);

    writeSidebarCollapsed(true);
    expect(readSidebarCollapsed()).toBe(true);

    writeSidebarCollapsed(false);
    expect(readSidebarCollapsed()).toBe(false);
  });

  it("starts expanded and does not fail when storage refuses", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(() => writeSidebarCollapsed(true)).not.toThrow();
    expect(readSidebarCollapsed()).toBe(false);
  });
});
