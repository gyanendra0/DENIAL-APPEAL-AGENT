import { describe, expect, it } from "vitest";

import { canReview, canUpload } from "../../src/auth/roles";

describe("roles", () => {
  it("lets a reviewer and an admin review a draft, not a viewer", () => {
    expect(canReview("reviewer")).toBe(true);
    expect(canReview("admin")).toBe(true);
    expect(canReview("viewer")).toBe(false);
    expect(canReview(undefined)).toBe(false);
  });

  it("lets a reviewer and an admin upload, not a viewer", () => {
    expect(canUpload("reviewer")).toBe(true);
    expect(canUpload("admin")).toBe(true);
    expect(canUpload("viewer")).toBe(false);
    expect(canUpload(undefined)).toBe(false);
  });
});
