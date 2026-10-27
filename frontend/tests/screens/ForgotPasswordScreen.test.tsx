import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const ASK = "POST /auth/password-reset";
const CONFIRM = "POST /auth/password-reset/confirm";
const NEW = "a long made-up password";
const ASKED = () =>
  jsonResponse(202, { detail: "if the email belongs to an account, a code is on its way" });
const NO_SESSION = () => jsonResponse(401, { detail: "not logged in" });

function stub(answers: Record<string, Answer>) {
  return stubApi({ "GET /me": NO_SESSION, ...answers });
}

async function askFor(email: string) {
  const user = userEvent.setup();
  if (email !== "") {
    await user.type(await screen.findByLabelText("Email"), email);
  }
  await user.click(await screen.findByRole("button", { name: "Send the code" }));
}

async function confirmWith(code: string, password: string) {
  const user = userEvent.setup();
  if (code !== "") {
    await user.type(await screen.findByLabelText("Code"), code);
  }
  if (password !== "") {
    await user.type(screen.getByLabelText(/^New password/), password);
  }
  await user.click(screen.getByRole("button", { name: "Set the password" }));
}

function callsTo(fetchStub: ReturnType<typeof stubApi>, path: string) {
  return fetchStub.mock.calls.filter(([input]) => String(input) === path);
}

describe("ForgotPasswordScreen", () => {
  it("asks for a code and says the same whatever the email", async () => {
    const fetchStub = stub({ [ASK]: ASKED });
    const { router } = renderAt("/app/forgot-password");

    await askFor(ADMIN.email);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "If the email belongs to an account, a code is on its way. Check your inbox.",
    );
    expect(sentBody(fetchStub, ASK)).toEqual({ email: ADMIN.email });
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Code")).toHaveAttribute("autoComplete", "one-time-code");
    expect(router.state.location.search).toBe("");
    expect(router.state.location.hash).toBe("");
  });

  it("sets the password with the email and code and goes to login with a note", async () => {
    const fetchStub = stub({ [ASK]: ASKED, [CONFIRM]: () => new Response(null, { status: 204 }) });
    const { router } = renderAt("/app/forgot-password");

    await askFor(ADMIN.email);
    await confirmWith("012345", NEW);

    expect(await screen.findByText("Your password is set. Log in with it.")).toHaveRole("status");
    expect(router.state.location.pathname).toBe("/app/login");
    expect(sentBody(fetchStub, CONFIRM)).toEqual({
      email: ADMIN.email,
      code: "012345",
      new_password: NEW,
    });
  });

  it("says a refused code is wrong or has expired and keeps the fields", async () => {
    stub({
      [ASK]: ASKED,
      [CONFIRM]: () => jsonResponse(400, { detail: "the code is wrong or has expired" }),
    });
    const { router } = renderAt("/app/forgot-password");

    await askFor(ADMIN.email);
    await confirmWith("000000", NEW);

    expect(await screen.findByRole("alert")).toHaveTextContent("The code is wrong or has expired.");
    expect(router.state.location.pathname).toBe("/app/forgot-password");
    expect(screen.getByLabelText("Code")).toHaveValue("000000");
  });

  it("names a wrong code and a short password, focuses the code and sends nothing", async () => {
    const fetchStub = stub({ [ASK]: ASKED });
    renderAt("/app/forgot-password");

    await askFor(ADMIN.email);
    await confirmWith("123", "too short");

    const code = screen.getByLabelText("Code");
    expect(code).toHaveAccessibleDescription("The code is 6 digits.");
    expect(code).toHaveFocus();
    expect(screen.getByLabelText(/^New password/)).toHaveAccessibleDescription(
      "12 to 128 characters. Use 12 to 128 characters.",
    );
    expect(callsTo(fetchStub, "/auth/password-reset/confirm")).toEqual([]);
  });

  it("asks again for a new code with the same email", async () => {
    const fetchStub = stub({ [ASK]: ASKED });
    renderAt("/app/forgot-password");

    await askFor(ADMIN.email);
    await userEvent.setup().click(screen.getByRole("button", { name: "Send a new code" }));

    expect(
      await screen.findByText("If the email belongs to an account, a new code is on its way."),
    ).toBeInTheDocument();
    const asks = callsTo(fetchStub, "/auth/password-reset");
    expect(asks).toHaveLength(2);
    expect(JSON.parse(String(asks[1]?.[1]?.body))).toEqual({ email: ADMIN.email });
    expect(screen.getByLabelText("Code")).toBeInTheDocument();
  });

  it("names an empty email, focuses it and sends nothing", async () => {
    const fetchStub = stub({ [ASK]: ASKED });
    renderAt("/app/forgot-password");

    await askFor("");

    const email = screen.getByLabelText("Email");
    expect(email).toHaveAccessibleDescription("Enter your email.");
    expect(email).toHaveFocus();
    expect(callsTo(fetchStub, "/auth/password-reset")).toEqual([]);
  });

  it("asks to wait after too many tries", async () => {
    stub({ [ASK]: () => jsonResponse(429, { detail: "too many tries" }) });
    renderAt("/app/forgot-password");

    await askFor(ADMIN.email);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
    expect(screen.getByLabelText("Email")).toHaveValue(ADMIN.email);
  });

  it("links back to the login screen", async () => {
    stub({});
    const { router } = renderAt("/app/forgot-password");

    await userEvent.setup().click(await screen.findByRole("link", { name: "Log in" }));

    expect(router.state.location.pathname).toBe("/app/login");
  });
});
