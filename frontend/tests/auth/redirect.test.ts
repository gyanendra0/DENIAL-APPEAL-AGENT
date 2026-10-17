import { describe, expect, it } from "vitest";

import { afterLoginPath } from "../../src/auth/redirect";

describe("afterLoginPath", () => {
  it.each([
    [undefined, "/app/worklist"],
    [null, "/app/worklist"],
    ["/app/upload", "/app/worklist"],
    [{}, "/app/worklist"],
    [{ from: 7 }, "/app/worklist"],
    [{ from: "https://elsewhere.test/app/x" }, "/app/worklist"],
    [{ from: "/admin" }, "/app/worklist"],
    [{ from: "/app/login" }, "/app/worklist"],
    [{ from: "/app/signup?x=1" }, "/app/worklist"],
    [{ from: "/app/upload" }, "/app/upload"],
  ])("sends state %j to %s", (state, expected) => {
    expect(afterLoginPath(state)).toBe(expected);
  });
});
