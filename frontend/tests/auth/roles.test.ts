import { describe, expect, it } from "vitest";

import { canDecide, canEdit, canUpload } from "../../src/auth/roles";

describe("roles", () => {
  it("lets every role upload, not a missing one", () => {
    expect(canUpload("viewer")).toBe(true);
    expect(canUpload("reviewer")).toBe(true);
    expect(canUpload("admin")).toBe(true);
    expect(canUpload(undefined)).toBe(false);
  });

  it("lets a reviewer and an admin edit a draft, not a viewer", () => {
    expect(canEdit("reviewer")).toBe(true);
    expect(canEdit("admin")).toBe(true);
    expect(canEdit("viewer")).toBe(false);
    expect(canEdit(undefined)).toBe(false);
  });

  it("lets only an admin approve or reject a draft", () => {
    expect(canDecide("admin")).toBe(true);
    expect(canDecide("reviewer")).toBe(false);
    expect(canDecide("viewer")).toBe(false);
    expect(canDecide(undefined)).toBe(false);
  });
});
