import { describe, expect, it } from "vitest";

import { PASSWORD_SET_STATE, isPasswordSetState } from "../../src/auth/passwordReset";

describe("isPasswordSetState", () => {
  it("knows the reset screen's note", () => {
    expect(isPasswordSetState(PASSWORD_SET_STATE)).toBe(true);
  });

  it("ignores any other state", () => {
    expect(isPasswordSetState(null)).toBe(false);
    expect(isPasswordSetState({ from: "/app/worklist" })).toBe(false);
    expect(isPasswordSetState({ passwordSet: "yes" })).toBe(false);
  });
});
