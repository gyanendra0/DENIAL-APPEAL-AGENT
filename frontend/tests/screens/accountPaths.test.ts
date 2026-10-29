import { describe, expect, it } from "vitest";

import { accountFormPath, readAccountForm } from "../../src/screens/accountPaths";

describe("accountPaths", () => {
  it("links to the account screen with the form to open", () => {
    expect(accountFormPath("password")).toBe("/app/account?open=password");
    expect(accountFormPath("delete")).toBe("/app/account?open=delete");
  });

  it("reads only the known forms", () => {
    expect(readAccountForm("password")).toBe("password");
    expect(readAccountForm("delete")).toBe("delete");
    expect(readAccountForm("everything")).toBeNull();
    expect(readAccountForm(null)).toBeNull();
  });
});
