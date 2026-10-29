import { describe, expect, it } from "vitest";

import { firstNameFromEmail } from "../../src/screens/firstName";

describe("firstNameFromEmail", () => {
  it.each([
    ["ada@example.test", "Ada"],
    ["ada.lovelace@example.test", "Ada"],
    ["grace_hopper@example.test", "Grace"],
    ["lin-wei@example.test", "Lin"],
    ["sam+appeals@example.test", "Sam"],
    ["kim2026@example.test", "Kim"],
    ["ADA@example.test", "Ada"],
  ])("makes %s into %s", (email, name) => {
    expect(firstNameFromEmail(email)).toBe(name);
  });

  it("keeps the whole part before the @ when the cut leaves nothing", () => {
    expect(firstNameFromEmail("2026.appeals@example.test")).toBe("2026.appeals");
  });
});
