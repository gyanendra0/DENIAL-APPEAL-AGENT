import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_KEY } from "../../src/auth/session";
import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";

const NO_SESSION = () => jsonResponse(401, { detail: "not logged in" });
const PASSWORD = "a long made-up password";

async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Account name"), "Made-up Clinic");
  await user.type(screen.getByLabelText("Email"), ADMIN.email);
  await user.type(screen.getByLabelText(/^Password/), PASSWORD);
  await user.click(screen.getByRole("button", { name: "Sign up" }));
}

describe("SignupScreen", () => {
  it("creates the account and goes to the code screen without logging in", async () => {
    const fetchStub = stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/signup": () => jsonResponse(201, ADMIN),
    });
    const { router, queryClient } = renderAt("/app/signup");

    await fillAndSubmit();

    expect(await screen.findByRole("heading", { name: "Confirm your email" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/verify-email");
    expect(queryClient.getQueryData(SESSION_KEY)).not.toEqual(ADMIN);
    expect(sentBody(fetchStub, "POST /auth/signup")).toEqual({
      account_name: "Made-up Clinic",
      email: ADMIN.email,
      password: PASSWORD,
    });
  });

  it("does not show the email on the code screen", async () => {
    stubApi({ "GET /me": NO_SESSION, "POST /auth/signup": () => jsonResponse(201, ADMIN) });
    renderAt("/app/signup");

    await fillAndSubmit();

    await screen.findByRole("heading", { name: "Confirm your email" });
    expect(document.body).not.toHaveTextContent(ADMIN.email);
  });

  it("says email cannot be sent on a 503", async () => {
    stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/signup": () =>
        jsonResponse(503, { detail: "we cannot send email right now; try again later" }),
    });
    const { router } = renderAt("/app/signup");

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We cannot send email right now. Try again later.",
    );
    expect(router.state.location.pathname).toBe("/app/signup");
  });

  it("asks the browser for the API's password length", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/signup");

    const password = await screen.findByLabelText(/^Password/);

    expect(password).toHaveAttribute("minLength", "12");
    expect(password).toHaveAttribute("maxLength", "128");
  });

  it("says which name is taken on a 409", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": NO_SESSION,
      "POST /auth/signup": () => jsonResponse(409, { detail: "the email is taken" }),
    });
    renderAt("/app/signup");

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent("The email is taken.");
  });

  it("names the refused field on a 422 without the text typed into it", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": NO_SESSION,
      "POST /auth/signup": () =>
        jsonResponse(422, {
          detail: [{ loc: ["body", "account_name"], type: "value_error", input: "Made-up Clinic" }],
        }),
    });
    renderAt("/app/signup");

    await fillAndSubmit();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Check these fields: account name.");
    expect(alert).not.toHaveTextContent("Made-up Clinic");
  });

  it("links to login", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    const { router } = renderAt("/app/signup");

    await userEvent.setup().click(screen.getByRole("link", { name: "Log in" }));

    expect(router.state.location.pathname).toBe("/app/login");
  });

  it("shows a sentence under each empty field, focuses the first and sends nothing", async () => {
    const fetchStub = stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/signup");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Sign up" }));

    const accountName = screen.getByLabelText("Account name");
    expect(accountName).toHaveAccessibleDescription("Enter the account name.");
    expect(accountName).toHaveFocus();
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription("Enter your email.");
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("refuses a short password before sending and keeps the rule in view", async () => {
    const fetchStub = stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/signup");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Account name"), "Made-up Clinic");
    await user.type(screen.getByLabelText("Email"), ADMIN.email);
    await user.type(screen.getByLabelText(/^Password/), "too short");
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    const password = screen.getByLabelText(/^Password/);
    expect(password).toHaveAccessibleDescription("12 to 128 characters. Use 12 to 128 characters.");
    expect(password).toHaveFocus();
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});
