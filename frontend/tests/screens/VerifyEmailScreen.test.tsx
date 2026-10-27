import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_KEY } from "../../src/auth/session";
import { jobKey } from "../../src/jobs/useJob";
import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const PATH = "/app/verify-email";
const VERIFY = "POST /auth/verify-email";
const RESEND = "POST /auth/verify-email/resend";
const NO_SESSION = () => jsonResponse(401, { detail: "not logged in" });
const EXPIRED = () =>
  jsonResponse(401, { detail: "the code screen has expired; log in again to get a new code" });

function stub(answers: Record<string, Answer>) {
  return stubApi({ ...EMPTY_WORKLIST, "GET /me": NO_SESSION, ...answers });
}

async function typeAndConfirm(code: string) {
  const user = userEvent.setup();
  if (code !== "") {
    await user.type(await screen.findByLabelText("Code"), code);
  }
  await user.click(await screen.findByRole("button", { name: "Confirm" }));
}

async function askForNewCode() {
  await userEvent.setup().click(await screen.findByRole("button", { name: "Send a new code" }));
}

function posts(fetchStub: ReturnType<typeof stubApi>) {
  return fetchStub.mock.calls.filter(([, init]) => init?.method === "POST");
}

describe("VerifyEmailScreen", () => {
  it("confirms the code, logs in and goes to the worklist", async () => {
    const fetchStub = stub({ [VERIFY]: () => jsonResponse(200, ADMIN) });
    const { router, queryClient } = renderAt(PATH);

    await typeAndConfirm("012345");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/worklist");
    expect(queryClient.getQueryData(SESSION_KEY)).toEqual(ADMIN);
    expect(sentBody(fetchStub, VERIFY)).toEqual({ code: "012345" });
  });

  it("drops data another user left in the cache", async () => {
    stub({ [VERIFY]: () => jsonResponse(200, ADMIN) });
    const { queryClient } = renderAt(PATH);
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    await typeAndConfirm("012345");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(queryClient.getQueryData(jobKey(7))).toBeUndefined();
  });

  it("asks the phone for a number pad and the code from the email", async () => {
    stub({});
    renderAt(PATH);

    const code = await screen.findByLabelText("Code");

    expect(code).toHaveAttribute("inputMode", "numeric");
    expect(code).toHaveAttribute("autoComplete", "one-time-code");
    expect(code).toHaveAttribute("maxLength", "6");
  });

  it("names a code that is not 6 digits, focuses it and sends nothing", async () => {
    const fetchStub = stub({});
    renderAt(PATH);

    await typeAndConfirm("12a45");

    const code = screen.getByLabelText("Code");
    expect(code).toHaveAccessibleDescription("The code is 6 digits.");
    expect(code).toHaveFocus();
    expect(posts(fetchStub)).toEqual([]);
  });

  it("says a refused code is wrong or has expired and stays", async () => {
    stub({ [VERIFY]: () => jsonResponse(400, { detail: "the code is wrong or has expired" }) });
    const { router, queryClient } = renderAt(PATH);

    await typeAndConfirm("000000");

    expect(await screen.findByRole("alert")).toHaveTextContent("The code is wrong or has expired.");
    expect(router.state.location.pathname).toBe(PATH);
    expect(queryClient.getQueryData(SESSION_KEY)).not.toEqual(ADMIN);
  });

  it("says the screen has expired on a 401, links to login and stops the resend", async () => {
    stub({ [VERIFY]: EXPIRED });
    const { router } = renderAt(PATH);

    await typeAndConfirm("012345");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This code screen has expired. Log in again to get a new code.",
    );
    expect(screen.getByRole("button", { name: "Send a new code" })).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("link", { name: "Log in" }));
    expect(router.state.location.pathname).toBe("/app/login");
  });

  it("says to wait after too many tries", async () => {
    stub({ [VERIFY]: () => jsonResponse(429, { detail: "too many tries" }) });
    renderAt(PATH);

    await typeAndConfirm("012345");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
  });

  it("asks for a new code and says it is on its way", async () => {
    const fetchStub = stub({ [RESEND]: () => jsonResponse(202, { detail: "on its way" }) });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("status")).toHaveTextContent("A new code is on its way.");
    expect(posts(fetchStub).map(([input]) => String(input))).toEqual(["/auth/verify-email/resend"]);
  });

  it("says how long to wait when a code was sent a moment ago", async () => {
    stub({
      [RESEND]: () =>
        new Response(JSON.stringify({ detail: "wait" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "42" },
        }),
    });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Wait 42 seconds before asking for another code.",
    );
  });

  it("tells a resend 429 without a wait like any other", async () => {
    stub({ [RESEND]: () => jsonResponse(429, { detail: "wait" }) });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many tries. Try again in a few minutes.",
    );
  });

  it("says the email is already confirmed and links to login", async () => {
    stub({
      [RESEND]: () => jsonResponse(409, { detail: "the email is already confirmed; log in" }),
    });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("alert")).toHaveTextContent("Your email is already confirmed.");
    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument();
  });

  it("says the screen has expired when the resend gets a 401", async () => {
    stub({ [RESEND]: EXPIRED });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This code screen has expired. Log in again to get a new code.",
    );
    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument();
  });

  it("says email cannot be sent over the daily cap", async () => {
    stub({ [RESEND]: () => jsonResponse(503, { detail: "we cannot send email right now" }) });
    renderAt(PATH);

    await askForNewCode();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We cannot send email right now. Try again later.",
    );
  });

  it("links back to sign-up for a wrong email", async () => {
    stub({});
    const { router } = renderAt(PATH);

    await userEvent.setup().click(await screen.findByRole("link", { name: "Sign up again" }));

    expect(router.state.location.pathname).toBe("/app/signup");
  });
});
