import { describe, expect, it } from "vitest";

import { ApiError } from "../../src/api/client";
import { describeError } from "../../src/screens/errorMessage";

describe("describeError", () => {
  it.each([
    [403, "forbidden", "Your role cannot do this."],
    [404, "not_found", "Not found."],
    [413, "too_large", "The file is too large."],
    [500, "server", "Something went wrong on the server. Try again later."],
  ] as const)("gives one sentence for a %i", (status, kind, expected) => {
    expect(describeError(new ApiError(status, kind, "detail from the API"))).toBe(expected);
  });

  it("uses the caller's own message for a status", () => {
    const error = new ApiError(401, "unauthorized", "email or password is wrong");

    expect(describeError(error, { 401: "Email or password is wrong." })).toBe(
      "Email or password is wrong.",
    );
  });

  it("names each refused field once", () => {
    const error = new ApiError(422, "invalid", "the request was not valid", [
      { loc: ["body", "email"], type: "value_error" },
      { loc: ["body", "email"], type: "string_too_long" },
      { loc: ["body", "account_name"], type: "missing" },
    ]);

    expect(describeError(error)).toBe("Check these fields: email, account name.");
  });

  it("falls back to a general line when a 422 names no field", () => {
    const error = new ApiError(422, "invalid", "the request was not valid", [
      { loc: ["body"], type: "json_invalid" },
      { loc: ["body", 0], type: "missing" },
    ]);

    expect(describeError(error)).toBe("Some fields are not valid.");
  });

  it("shows the API's fixed sentence of a 422 as one sentence", () => {
    const error = new ApiError(422, "invalid", "the file is empty");

    expect(describeError(error)).toBe("The file is empty.");
  });

  it("treats an error that is not from the API as a server error", () => {
    expect(describeError(new Error("boom"))).toBe(
      "Something went wrong on the server. Try again later.",
    );
  });
});
