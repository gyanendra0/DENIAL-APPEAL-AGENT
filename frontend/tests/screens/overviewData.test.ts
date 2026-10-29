import { describe, expect, it } from "vitest";

import { percent, total } from "../../src/screens/overviewData";

describe("overviewData", () => {
  it("adds up the counts", () => {
    expect(total({ a: 1, b: 2, c: 0 })).toBe(3);
    expect(total({})).toBe(0);
  });

  it("gives a whole percent, and 0 of nothing", () => {
    expect(percent(2, 3)).toBe(67);
    expect(percent(1, 3)).toBe(33);
    expect(percent(0, 0)).toBe(0);
  });
});
