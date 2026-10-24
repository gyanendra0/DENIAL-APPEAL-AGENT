import { describe, expect, it } from "vitest";

import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkLogin,
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
