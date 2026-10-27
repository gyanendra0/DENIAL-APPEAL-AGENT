import { describe, expect, it } from "vitest";

import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkGoogleSignup,
  checkCode,
  checkLogin,
  checkPasswordChange,
  checkResetAsk,
  checkResetConfirm,
  checkSignup,
  focusFirstError,
} from "../../src/auth/credentialCheck";

const PASSWORD = "a long made-up password";

describe("checkLogin", () => {
  it("accepts an email and a password", () => {
    expect(checkLogin({ email: "ada@example.test", password: "pw" })).toEqual({});
  });

  it("names both empty fields, email first", () => {
    const errors = checkLogin({ email: "", password: "" });

    expect(errors).toEqual({ email: "Enter your email.", password: "Enter your password." });
    expect(Object.keys(errors)).toEqual(["email", "password"]);
  });

  it.each(["ada", "ada@", "@example.test", "ada @example.test", "ada@@example.test"])(
    "refuses the email %s",
    (email) => {
      expect(checkLogin({ email, password: "pw" }).email).toBe(
        "Enter an email like name@example.com.",
      );
    },
  );

  it("leaves an email the API may refuse to the API", () => {
    expect(checkLogin({ email: "ada@made-up", password: "pw" })).toEqual({});
  });

  it("never repeats what was typed", () => {
    expect(checkLogin({ email: "secret-name", password: "" }).email).not.toContain("secret-name");
  });
});

describe("checkSignup", () => {
  const VALID = { accountName: "Made-up Clinic", email: "ada@example.test", password: PASSWORD };

  it("accepts a full sign-up", () => {
    expect(checkSignup(VALID)).toEqual({});
  });

  it("names every empty field in screen order", () => {
    const errors = checkSignup({ accountName: "  ", email: "", password: "" });

    expect(Object.keys(errors)).toEqual(["accountName", "email", "password"]);
    expect(errors.accountName).toBe("Enter the account name.");
  });

  it("accepts the shortest and longest password and refuses one character outside", () => {
    const sentence = `Use ${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`;
    const of = (length: number) => checkSignup({ ...VALID, password: "x".repeat(length) });

    expect(of(MIN_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MAX_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MIN_PASSWORD_CHARACTERS - 1).password).toBe(sentence);
    expect(of(MAX_PASSWORD_CHARACTERS + 1).password).toBe(sentence);
  });

  it("counts characters as the API does, not UTF-16 units", () => {
    // Six emoji are twelve UTF-16 units but six characters in Python.
    expect(checkSignup({ ...VALID, password: "😀".repeat(6) }).password).toBeDefined();
    expect(checkSignup({ ...VALID, password: "😀".repeat(12) })).toEqual({});
  });
});

describe("checkPasswordChange", () => {
  const VALID = { currentPassword: "pw", newPassword: PASSWORD };

  it("accepts a change, leaving the current password's length to the API", () => {
    expect(checkPasswordChange(VALID)).toEqual({});
  });

  it("names both empty fields in screen order", () => {
    const errors = checkPasswordChange({ currentPassword: "", newPassword: "" });

    expect(Object.keys(errors)).toEqual(["currentPassword", "newPassword"]);
    expect(errors.currentPassword).toBe("Enter your current password.");
  });

  it("refuses a new password one character outside the API's limits", () => {
    const sentence = `Use ${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`;
    const of = (length: number) =>
      checkPasswordChange({ ...VALID, newPassword: "x".repeat(length) });

    expect(of(MIN_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MAX_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MIN_PASSWORD_CHARACTERS - 1).newPassword).toBe(sentence);
    expect(of(MAX_PASSWORD_CHARACTERS + 1).newPassword).toBe(sentence);
  });

  it("never repeats what was typed", () => {
    expect(checkPasswordChange({ ...VALID, newPassword: "secret" }).newPassword).not.toContain(
      "secret",
    );
  });
});

describe("checkGoogleSignup", () => {
  it("accepts an account name", () => {
    expect(checkGoogleSignup({ accountName: "Made-up Clinic" })).toEqual({});
  });

  it("names an empty or blank account name", () => {
    expect(checkGoogleSignup({ accountName: "  " }).accountName).toBe("Enter the account name.");
  });
});

describe("checkResetAsk", () => {
  it("accepts an email", () => {
    expect(checkResetAsk({ email: "ada@example.test" })).toEqual({});
  });

  it("names an empty or malformed email", () => {
    expect(checkResetAsk({ email: " " }).email).toBe("Enter your email.");
    expect(checkResetAsk({ email: "ada" }).email).toBe("Enter an email like name@example.com.");
  });
});

describe("checkResetConfirm", () => {
  it("refuses a new password one character outside the API's limits", () => {
    const sentence = `Use ${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`;
    const of = (length: number) =>
      checkResetConfirm({ code: "012345", newPassword: "x".repeat(length) });

    expect(of(MIN_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MAX_PASSWORD_CHARACTERS)).toEqual({});
    expect(of(MIN_PASSWORD_CHARACTERS - 1).newPassword).toBe(sentence);
    expect(of(MAX_PASSWORD_CHARACTERS + 1).newPassword).toBe(sentence);
  });

  it("names both wrong fields, code first", () => {
    const errors = checkResetConfirm({ code: "", newPassword: "" });

    expect(Object.keys(errors)).toEqual(["code", "newPassword"]);
    expect(errors.code).toBe("Enter the code from the email.");
  });
});

describe("checkCode", () => {
  it("accepts six digits, leading zeros kept, and spaces around them", () => {
    expect(checkCode({ code: "000123" })).toEqual({});
    expect(checkCode({ code: " 123456 " })).toEqual({});
  });

  it("asks for a code when none is typed", () => {
    expect(checkCode({ code: "  " }).code).toBe("Enter the code from the email.");
  });

  it.each(["12345", "1234567", "12a456", "123 456", "１２３４５６"])(
    "refuses the code %s",
    (code) => {
      expect(checkCode({ code }).code).toBe("The code is 6 digits.");
    },
  );
});

describe("focusFirstError", () => {
  function formWith(...names: string[]): HTMLFormElement {
    const form = document.createElement("form");
    for (const name of names) {
      const input = document.createElement("input");
      input.name = name;
      form.append(input);
    }
    document.body.append(form);
    return form;
  }

  it("focuses the first wrong field and says there was one", () => {
    const form = formWith("email", "password");

    expect(focusFirstError(form, { password: "Enter your password." })).toBe(true);
    expect(document.activeElement).toBe(form.elements.namedItem("password"));
    form.remove();
  });

  it("says there was none when nothing is wrong", () => {
    const form = formWith("email");

    expect(focusFirstError(form, {})).toBe(false);
    form.remove();
  });
});
