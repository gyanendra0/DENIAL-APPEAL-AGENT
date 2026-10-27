import { describe, expect, it } from "vitest";

import { ApiError } from "../../src/api/client";
import { EMAIL_NOT_VERIFIED, isEmailNotVerified, waitSentence } from "../../src/auth/emailCode";

describe("isEmailNotVerified", () => {
  it("knows the login's 403 with the API's code", () => {
    const error = new ApiError(403, "forbidden", "confirm your email first", [], [], {
      code: EMAIL_NOT_VERIFIED,
    });

    expect(isEmailNotVerified(error)).toBe(true);
  });

  it("ignores a 403 without the code, another status and other errors", () => {
    expect(isEmailNotVerified(new ApiError(403, "forbidden", "no"))).toBe(false);
    expect(
      isEmailNotVerified(
        new ApiError(401, "unauthorized", "no", [], [], { code: EMAIL_NOT_VERIFIED }),
      ),
    ).toBe(false);
    expect(isEmailNotVerified(new Error("boom"))).toBe(false);
  });
});

describe("waitSentence", () => {
  it("counts seconds under a minute", () => {
    expect(waitSentence(1)).toBe("Wait 1 second before asking for another code.");
    expect(waitSentence(59)).toBe("Wait 59 seconds before asking for another code.");
  });

  it("rounds a minute or more up to whole minutes", () => {
    expect(waitSentence(60)).toBe("Wait 1 minute before asking for another code.");
    expect(waitSentence(61)).toBe("Wait 2 minutes before asking for another code.");
    expect(waitSentence(3600)).toBe("Wait 60 minutes before asking for another code.");
  });
});
