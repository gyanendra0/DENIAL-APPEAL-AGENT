import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { jobKey } from "../../src/jobs/useJob";
import type { UserResponse } from "../../src/api/types";
import { SESSION_KEY } from "../../src/auth/session";
import {
  ADMIN,
  EMPTY_WORKLIST,
  REVIEWER,
  jsonResponse,
  renderAt,
  sentBody,
  stubApi,
} from "../helpers";

const DONE_PAGE = "GET /worklist?limit=50&status=done";

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
      ...EMPTY_WORKLIST,
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

  it("drops data another user left in the cache", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": NO_SESSION,
      "POST /auth/login": () => jsonResponse(200, ADMIN),
    });
    const { queryClient } = renderAt("/app/login");
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    await fillAndSubmit(ADMIN.email, "a long made-up password");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(queryClient.getQueryData(jobKey(7))).toBeUndefined();
  });

  it("goes back to the page the guard remembered", async () => {
    let loggedIn = false;
    stubApi({
      ...EMPTY_WORKLIST,
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

  it("empties the cache when it opens after the session has ended", async () => {
    stubApi({
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /worklist?limit=50": () => jsonResponse(401, { detail: "not logged in" }),
    });
    const { queryClient } = renderAt("/app/worklist");
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(queryClient.getQueryData(SESSION_KEY)).toBeNull();
    expect(queryClient.getQueryData(jobKey(7))).toBeUndefined();
  });

  // The session ends on the done filter (a 401), then `next` logs in.
  async function logInAfterSessionEnds(next: UserResponse) {
    let loggedIn = true;
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      [DONE_PAGE]: () =>
        loggedIn
          ? jsonResponse(401, { detail: "not logged in" })
          : jsonResponse(200, { items: [], next_before: null }),
      "POST /auth/login": () => jsonResponse(200, next),
    });
    const { router } = renderAt("/app/worklist?status=done");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    loggedIn = false;

    await fillAndSubmit(next.email, "a long made-up password");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    return router.state.location.pathname + router.state.location.search;
  }

  it("brings the same user back to the page they had open", async () => {
    expect(await logInAfterSessionEnds(ADMIN)).toBe("/app/worklist?status=done");
  });

  it("sends a different user home, not to the page the first one had open", async () => {
    expect(await logInAfterSessionEnds({ ...REVIEWER, email: "bo@example.test" })).toBe(
      "/app/worklist",
    );
  });

  it("says the email or password is wrong on a 401", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": NO_SESSION,
      "POST /auth/login": () => jsonResponse(401, { detail: "email or password is wrong" }),
    });
    renderAt("/app/login");

    await fillAndSubmit(ADMIN.email, "wrong password");

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is wrong.");
  });

  it("names the refused field on a 422 without the text typed into it", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
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
      ...EMPTY_WORKLIST,
      "GET /me": NO_SESSION,
      "POST /auth/login": () => Promise.reject(new TypeError("offline")),
    });
    renderAt("/app/login");

    await fillAndSubmit(ADMIN.email, "pw");

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
  });

  it("links to sign-up", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    const { router } = renderAt("/app/login");

    await userEvent.setup().click(screen.getByRole("link", { name: "Sign up" }));

    expect(router.state.location.pathname).toBe("/app/signup");
  });

  it("shows a sentence under each empty field, focuses the first and sends nothing", async () => {
    const fetchStub = stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/login");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Log in" }));

    const email = screen.getByLabelText("Email");
    expect(email).toHaveAccessibleDescription("Enter your email.");
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveFocus();
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription("Enter your password.");
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("asks for an email with an @ before sending", async () => {
    const fetchStub = stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/login");

    await fillAndSubmit("ada", "a long made-up password");

    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
      "Enter an email like name@example.com.",
    );
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("shows the password on request", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION });
    renderAt("/app/login");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Show password" }));

    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "text");
  });
});
