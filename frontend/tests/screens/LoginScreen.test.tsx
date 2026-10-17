import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";

const NO_SESSION = () => jsonResponse(401, { detail: "not logged in" });

async function fillAndSubmit(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Log in" }));
}

describe("LoginScreen", () => {
  it("logs in and goes to the worklist", async () => {
    const fetchStub = stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/login": () => jsonResponse(200, ADMIN),
    });
    const { router } = renderAt("/app/login");

    await fillAndSubmit(ADMIN.email, "a long made-up password");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/worklist");
    expect(screen.getByText(/Made-up Clinic/)).toBeInTheDocument();
    expect(sentBody(fetchStub, "POST /auth/login")).toEqual({
      email: ADMIN.email,
      password: "a long made-up password",
    });
  });

  it("goes back to the page the guard remembered", async () => {
    let loggedIn = false;
    stubApi({
      "GET /me": () => (loggedIn ? jsonResponse(200, ADMIN) : NO_SESSION()),
      "POST /auth/login": () => {
        loggedIn = true;
        return jsonResponse(200, ADMIN);
      },
    });
    const { router } = renderAt("/app/worklist?status=done");

    await fillAndSubmit(ADMIN.email, "a long made-up password");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname + router.state.location.search).toBe(
      "/app/worklist?status=done",
    );
  });

  it("says the email or password is wrong on a 401", async () => {
    stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/login": () => jsonResponse(401, { detail: "email or password is wrong" }),
    });
    renderAt("/app/login");

    await fillAndSubmit(ADMIN.email, "wrong password");

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is wrong.");
  });

  it("names the refused field on a 422 without the text typed into it", async () => {
    stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/login": () =>
        jsonResponse(422, {
          detail: [
            { loc: ["body", "email"], type: "value_error", msg: "bad", input: "ada@made-up" },
          ],
        }),
    });
    renderAt("/app/login");

    // The browser accepts this shape; the stubbed API refuses it.
    await fillAndSubmit("ada@made-up", "pw");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Check these fields: email.");
    expect(alert).not.toHaveTextContent("ada@made-up");
  });

  it("says when the API cannot be reached", async () => {
    stubApi({
      "GET /me": NO_SESSION,
      "POST /auth/login": () => Promise.reject(new TypeError("offline")),
    });
    renderAt("/app/login");

    await fillAndSubmit(ADMIN.email, "pw");

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
  });

  it("links to sign-up", async () => {
    stubApi({ "GET /me": NO_SESSION });
    const { router } = renderAt("/app/login");

    await userEvent.setup().click(screen.getByRole("link", { name: "Sign up" }));

    expect(router.state.location.pathname).toBe("/app/signup");
  });
});
