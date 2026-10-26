import { describe, expect, it } from "vitest";

import { PASSWORD_SET_STATE, isPasswordSetState, tokenFromHash } from "../../src/auth/resetLink";

describe("tokenFromHash", () => {
  it("reads the token from the link's fragment", () => {
    expect(tokenFromHash("#token=a.made-up.token")).toBe("a.made-up.token");
  });

  it("keeps a token that has URL-safe base64 characters", () => {
    expect(tokenFromHash("#token=ab-c_d.e")).toBe("ab-c_d.e");
  });

  it("returns null for no fragment, another key or an empty token", () => {
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#other=x")).toBeNull();
    expect(tokenFromHash("#token=")).toBeNull();
  });
});

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
