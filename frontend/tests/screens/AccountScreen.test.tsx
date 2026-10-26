import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { NO_RECENT, VIEWER, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
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
