import { describe, expect, it } from "vitest";

import { googleRefusalMessage } from "../../src/auth/googleSignIn";

describe("googleRefusalMessage", () => {
  it.each([
    ["failed", "Google sign-in did not finish. Try again."],
    [
      "not_verified",
      "Google has not verified this email. Use another Google account or your password.",
    ],
    ["use_password", "This email already has a login here. Log in with your password."],
    ["linked_elsewhere", "This Google account is linked to another login."],
    ["expired", "Google sign-in took too long, or was started again in another tab. Try again."],
  ])("gives a fixed sentence for the code %s", (code, sentence) => {
    expect(googleRefusalMessage(`?google=${code}`)).toBe(sentence);
  });

  it("says nothing when there is no code", () => {
    expect(googleRefusalMessage("")).toBeNull();
    expect(googleRefusalMessage("?next=1")).toBeNull();
  });

  it("never shows an unknown code, nor a name every object has", () => {
    expect(googleRefusalMessage("?google=%3Cb%3Ehello%3C%2Fb%3E")).toBeNull();
    expect(googleRefusalMessage("?google=toString")).toBeNull();
    expect(googleRefusalMessage("?google=")).toBeNull();
  });
});
