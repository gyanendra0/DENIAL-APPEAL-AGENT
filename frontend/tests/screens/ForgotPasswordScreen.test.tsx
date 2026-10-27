import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const ASKED = () =>
  jsonResponse(202, { detail: "if the email belongs to an account, a link is on its way" });

function stubAsk(answer: Answer) {
  return stubApi({ "POST /auth/password-reset": answer });
}

async function fillAndSubmit(email: string) {
  const user = userEvent.setup();
  if (email !== "") {
    await user.type(await screen.findByLabelText("Email"), email);
  }
  await user.click(await screen.findByRole("button", { name: "Send the link" }));
}

describe("ForgotPasswordScreen", () => {
  it("asks for a link and says the same whatever the email", async () => {
    const fetchStub = stubAsk(ASKED);
    renderAt("/app/forgot-password");

    await fillAndSubmit(ADMIN.email);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "If the email belongs to an account, a link is on its way. Check your inbox.",
    );
    expect(sentBody(fetchStub, "POST /auth/password-reset")).toEqual({ email: ADMIN.email });
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });

  it("names an empty email, focuses it and sends nothing", async () => {
    const fetchStub = stubAsk(ASKED);
    renderAt("/app/forgot-password");

    await fillAndSubmit("");

    const email = screen.getByLabelText("Email");
    expect(email).toHaveAccessibleDescription("Enter your email.");
    expect(email).toHaveFocus();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("asks to wait after too many tries", async () => {
    stubAsk(() => jsonResponse(429, { detail: "too many tries" }));
    renderAt("/app/forgot-password");

    await fillAndSubmit(ADMIN.email);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
    expect(screen.getByLabelText("Email")).toHaveValue(ADMIN.email);
  });

  it("links back to the login screen", async () => {
    stubApi({ "GET /me": () => jsonResponse(401, { detail: "not logged in" }) });
    const { router } = renderAt("/app/forgot-password");

    await userEvent.setup().click(await screen.findByRole("link", { name: "Log in" }));

    expect(router.state.location.pathname).toBe("/app/login");
  });
});
