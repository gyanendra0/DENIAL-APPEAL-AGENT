import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const PATH = "/app/google/name-account";
const PENDING = () => jsonResponse(200, { email: ADMIN.email, account_name: ADMIN.account_name });
const EXPIRED = () =>
  jsonResponse(401, { detail: "the Google sign-up has expired; continue with Google again" });

function stubSignup(info: Answer, create?: Answer) {
  return stubApi({
    ...EMPTY_WORKLIST,
    "GET /auth/google/signup": info,
    ...(create === undefined ? {} : { "POST /auth/google/signup": create }),
  });
}

async function join() {
  await userEvent.setup().click(await screen.findByRole("button", { name: "Join" }));
}

function posts(fetchStub: ReturnType<typeof stubApi>): number {
  return fetchStub.mock.calls.filter(([, init]) => init?.method === "POST").length;
}

describe("GoogleJoinScreen", () => {
  it("asks to join the account as the Google email, then joins and goes to the worklist", async () => {
    const fetchStub = stubSignup(PENDING, () => jsonResponse(201, ADMIN));
    const { router } = renderAt(PATH);

    expect(await screen.findByText(ADMIN.email)).toBeInTheDocument();
    expect(screen.getByText(ADMIN.account_name)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await join();

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/worklist");
    expect(sentBody(fetchStub, "POST /auth/google/signup")).toEqual({});
  });

  it("sends nothing until Join is clicked", async () => {
    const fetchStub = stubSignup(PENDING);
    renderAt(PATH);

    await screen.findByRole("button", { name: "Join" });

    expect(posts(fetchStub)).toBe(0);
  });

  it("says sign-up is not open on a 403 and stays on the screen", async () => {
    stubSignup(PENDING, () => jsonResponse(403, { detail: "sign-up is not open for this email" }));
    const { router } = renderAt(PATH);

    await join();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign-up is not open for this email.",
    );
    expect(router.state.location.pathname).toBe(PATH);
  });

  it("says what is taken on a 409", async () => {
    stubSignup(PENDING, () => jsonResponse(409, { detail: "the email is taken" }));
    renderAt(PATH);

    await join();

    expect(await screen.findByRole("alert")).toHaveTextContent("The email is taken.");
  });

  it("says the server failed when Join gets a 500", async () => {
    stubSignup(PENDING, () => jsonResponse(500, {}));
    renderAt(PATH);

    await join();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server. Try again later.",
    );
    expect(screen.getByRole("button", { name: "Join" })).toBeEnabled();
  });

  it("says the sign-up has expired when it opens without one", async () => {
    stubSignup(EXPIRED);
    renderAt(PATH);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Google sign-up has expired. Continue with Google again.",
    );
    expect(screen.queryByRole("button", { name: "Join" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to log in" })).toHaveAttribute(
      "href",
      "/app/login",
    );
  });

  it("says the sign-up has expired when it runs out before Join is clicked", async () => {
    stubSignup(PENDING, EXPIRED);
    renderAt(PATH);

    await join();

    expect(await screen.findByRole("alert")).toHaveTextContent("The Google sign-up has expired.");
    expect(screen.queryByRole("button", { name: "Join" })).not.toBeInTheDocument();
  });

  it("says when the pending sign-up cannot be read", async () => {
    stubSignup(() => jsonResponse(500, {}));
    renderAt(PATH);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server. Try again later.",
    );
  });
});
