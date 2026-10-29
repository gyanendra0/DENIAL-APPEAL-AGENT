import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { UserResponse } from "../../src/api/types";
import {
  ADMIN,
  INDIVIDUAL,
  NO_RECENT,
  VIEWER,
  jsonResponse,
  renderAt,
  sentBody,
  stubApi,
} from "../helpers";
import type { Answer } from "../helpers";

const CURRENT = "a made-up current password";
const NEW = "a made-up new password";

function stubChange(answer: Answer) {
  return stubApi({
    ...NO_RECENT,
    "GET /me": () => jsonResponse(200, VIEWER),
    "POST /me/password": answer,
  });
}

async function fillAndSubmit(current = CURRENT, next = NEW) {
  const user = userEvent.setup();
  if (current !== "") {
    await user.type(await screen.findByLabelText(/^Current password/), current);
  }
  if (next !== "") {
    await user.type(screen.getByLabelText(/^New password/), next);
  }
  await user.click(screen.getByRole("button", { name: "Change password" }));
}

describe("AccountScreen", () => {
  it("changes the password for a viewer, clears the fields and says other sessions ended", async () => {
    const fetchStub = stubChange(() => new Response(null, { status: 204 }));
    renderAt("/app/account");

    await fillAndSubmit();

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Your password is changed. Other sessions are logged out.",
    );
    expect(sentBody(fetchStub, "POST /me/password")).toEqual({
      current_password: CURRENT,
      new_password: NEW,
    });
    expect(screen.getByLabelText(/^Current password/)).toHaveValue("");
    expect(screen.getByLabelText(/^New password/)).toHaveValue("");
  });

  it("names both empty fields, focuses the first and sends nothing", async () => {
    const fetchStub = stubChange(() => new Response(null, { status: 204 }));
    renderAt("/app/account");
    await screen.findByRole("heading", { name: "Change password" });

    await fillAndSubmit("", "");

    expect(screen.getByText("Enter your current password.")).toBeInTheDocument();
    expect(screen.getByText("Use 12 to 128 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Current password/)).toHaveFocus();
    expect(fetchStub.mock.calls.some(([input]) => String(input) === "/me/password")).toBe(false);
  });

  it("refuses a short new password in the browser", async () => {
    const fetchStub = stubChange(() => new Response(null, { status: 204 }));
    renderAt("/app/account");

    await fillAndSubmit(CURRENT, "short");

    expect(screen.getByText("Use 12 to 128 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText(/^New password/)).toHaveFocus();
    expect(fetchStub.mock.calls.some(([input]) => String(input) === "/me/password")).toBe(false);
  });

  it("says the current password is wrong on a 400 and keeps what was typed", async () => {
    stubChange(() => jsonResponse(400, { detail: "the current password is wrong" }));
    renderAt("/app/account");

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The current password is wrong.");
    expect(alert).not.toHaveTextContent(CURRENT);
    expect(alert).not.toHaveTextContent(NEW);
    expect(screen.getByLabelText(/^New password/)).toHaveValue(NEW);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says a Google sign-up has no password yet on that 400", async () => {
    stubChange(() =>
      jsonResponse(400, {
        detail: "this login has no password yet; use Forgot password to set one",
      }),
    );
    renderAt("/app/account");

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This login has no password yet; use Forgot password to set one.",
    );
  });

  it("asks to wait after too many tries", async () => {
    stubChange(() => jsonResponse(429, { detail: "too many failed logins" }));
    renderAt("/app/account");

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
  });

  it("names the field a 422 refused, not what was typed", async () => {
    stubChange(() =>
      jsonResponse(422, {
        detail: [{ loc: ["body", "new_password"], type: "string_too_long", input: NEW }],
      }),
    );
    renderAt("/app/account");

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Check these fields: new password.");
    expect(alert).not.toHaveTextContent(NEW);
  });

  it("gives each password field its own show button", async () => {
    stubChange(() => new Response(null, { status: 204 }));
    renderAt("/app/account");

    expect(
      await screen.findByRole("button", { name: "Show current password" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show new password" })).toBeInTheDocument();
  });
});

function stubDelete(answer: Answer, user: UserResponse = INDIVIDUAL) {
  return stubApi({
    ...NO_RECENT,
    "GET /me": () => jsonResponse(200, user),
    "POST /me/delete-account": answer,
  });
}

// Opens the confirm step and sends it with the password typed, or with the field left empty.
async function confirmDelete(password: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Delete my account" }));
  if (password !== "") {
    await user.type(screen.getByLabelText(/^Password/), password);
  }
  await user.click(screen.getByRole("button", { name: "Delete for good" }));
}

describe("AccountScreen: delete my account", () => {
  it.each([
    ["the shared account's admin", ADMIN],
    ["the shared account's viewer", VIEWER],
  ])("is not offered to %s", async (_label, user) => {
    stubDelete(() => new Response(null, { status: 204 }), user);
    renderAt("/app/account");

    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete my account" })).not.toBeInTheDocument();
  });

  it("asks to confirm first and says what is deleted, and Cancel sends nothing", async () => {
    const fetchStub = stubDelete(() => new Response(null, { status: 204 }));
    const user = userEvent.setup();
    renderAt("/app/account");

    await user.click(await screen.findByRole("button", { name: "Delete my account" }));
    expect(screen.getByText(/every letter you uploaded and every draft/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Password/), CURRENT);
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("button", { name: "Delete for good" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete my account" }));
    expect(screen.getByLabelText(/^Password/)).toHaveValue("");
    expect(fetchStub.mock.calls.filter(([path]) => path === "/me/delete-account")).toHaveLength(0);
  });

  it("deletes with the password and goes to login", async () => {
    const fetchStub = stubDelete(() => new Response(null, { status: 204 }));
    const { router } = renderAt("/app/account");

    await confirmDelete(CURRENT);

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
    expect(sentBody(fetchStub, "POST /me/delete-account")).toEqual({ password: CURRENT });
  });

  it("sends no password when the field is left empty (a Google-only login)", async () => {
    const fetchStub = stubDelete(() => new Response(null, { status: 204 }));
    renderAt("/app/account");

    await confirmDelete("");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(sentBody(fetchStub, "POST /me/delete-account")).toEqual({ password: null });
  });

  it.each([
    [400, "the password is wrong", "The password is wrong."],
    [
      403,
      "log in again, then delete your account within 10 minutes",
      "Log in again, then delete your account within 10 minutes.",
    ],
    [
      409,
      "a letter is still being read; wait until it is finished, then delete",
      "A letter is still being read; wait until it is finished, then delete.",
    ],
    [429, "too many failed logins; try again later", "Too many tries."],
  ])("shows the refusal of a delete answered %i and stays", async (status, detail, message) => {
    stubDelete(() => jsonResponse(status, { detail }));
    const { router } = renderAt("/app/account");

    await confirmDelete(CURRENT);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert).not.toHaveTextContent(CURRENT);
    expect(router.state.location.pathname).toBe("/app/account");
    expect(screen.getByRole("button", { name: "Delete for good" })).toBeEnabled();
  });
});
