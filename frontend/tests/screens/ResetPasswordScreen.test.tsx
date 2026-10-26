import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const TOKEN = "a.made-up.token";
const LINK = `/app/reset-password#token=${TOKEN}`;
const NEW = "a long made-up password";
const CONFIRM = "POST /auth/password-reset/confirm";

function stubConfirm(answer: Answer) {
  return stubApi({
    "GET /me": () => jsonResponse(401, { detail: "not logged in" }),
    [CONFIRM]: answer,
  });
}

async function fillAndSubmit(password = NEW) {
  const user = userEvent.setup();
  if (password !== "") {
    await user.type(await screen.findByLabelText(/^New password/), password);
  }
  await user.click(screen.getByRole("button", { name: "Set the password" }));
}

function confirmCalls(fetchStub: ReturnType<typeof stubApi>) {
  return fetchStub.mock.calls.filter(([input]) => String(input) === "/auth/password-reset/confirm");
}

describe("ResetPasswordScreen", () => {
  it("sets the password with the link's token and goes to login with a note", async () => {
    const fetchStub = stubConfirm(() => new Response(null, { status: 204 }));
    const { router } = renderAt(LINK);

    await fillAndSubmit();

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Your password is set. Log in with it.",
    );
    expect(router.state.location.pathname).toBe("/app/login");
    expect(sentBody(fetchStub, CONFIRM)).toEqual({ token: TOKEN, new_password: NEW });
  });

  it("takes the token out of the address at once", async () => {
    stubConfirm(() => new Response(null, { status: 204 }));
    const { router } = renderAt(LINK);

    await waitFor(() => expect(router.state.location.hash).toBe(""));
    expect(router.state.location.pathname).toBe("/app/reset-password");
  });

  it("says a refused link is not valid and offers a new one", async () => {
    stubConfirm(() => jsonResponse(400, { detail: "the reset link is not valid or has expired" }));
    const { router } = renderAt(LINK);

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The reset link is not valid or has expired.",
    );
    await userEvent.setup().click(screen.getByRole("link", { name: "Ask for a new link" }));
    expect(router.state.location.pathname).toBe("/app/forgot-password");
  });

  it("says a link without a token is not valid and sends nothing", async () => {
    const fetchStub = stubConfirm(() => new Response(null, { status: 204 }));
    renderAt("/app/reset-password");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The reset link is not valid or has expired.",
    );
    expect(screen.getByRole("button", { name: "Set the password" })).toBeDisabled();
    expect(confirmCalls(fetchStub)).toHaveLength(0);
  });

  it("names a short password, focuses it and sends nothing", async () => {
    const fetchStub = stubConfirm(() => new Response(null, { status: 204 }));
    renderAt(LINK);

    await fillAndSubmit("short");

    const field = screen.getByLabelText(/^New password/);
    expect(field).toHaveAccessibleDescription(/Use 12 to 128 characters\./);
    expect(field).toHaveFocus();
    expect(confirmCalls(fetchStub)).toHaveLength(0);
  });

  it("asks to wait after too many tries", async () => {
    stubConfirm(() => jsonResponse(429, { detail: "too many tries" }));
    renderAt(LINK);

    await fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
    expect(screen.queryByRole("link", { name: "Ask for a new link" })).not.toBeInTheDocument();
  });
});
