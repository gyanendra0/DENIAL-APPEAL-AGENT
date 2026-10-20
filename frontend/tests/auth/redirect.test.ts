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
    [{ from: "/app/upload", userId: 1 }, "/app/upload"],
    [{ from: "/app/upload", userId: 2 }, "/app/worklist"],
    [{ from: "/app/upload", userId: "1" }, "/app/worklist"],
  ])("sends state %j to %s for user 1", (state, expected) => {
    expect(afterLoginPath(state, 1)).toBe(expected);
  });
});
