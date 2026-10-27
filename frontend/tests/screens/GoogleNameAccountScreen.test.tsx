import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, sentBody, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const PATH = "/app/google/name-account";
const PENDING = () => jsonResponse(200, { email: ADMIN.email });
const EXPIRED = () =>
  jsonResponse(401, { detail: "the Google sign-up has expired; continue with Google again" });

function stubSignup(info: Answer, create?: Answer) {
  return stubApi({
    ...EMPTY_WORKLIST,
    "GET /auth/google/signup": info,
    ...(create === undefined ? {} : { "POST /auth/google/signup": create }),
  });
}

async function nameAndSubmit(name = "Made-up Clinic") {
  const user = userEvent.setup();
  if (name !== "") {
    await user.type(await screen.findByLabelText("Account name"), name);
  }
  await user.click(await screen.findByRole("button", { name: "Create the account" }));
}

describe("GoogleNameAccountScreen", () => {
  it("shows the Google email, creates the account and goes to the worklist", async () => {
    const fetchStub = stubSignup(PENDING, () => jsonResponse(201, ADMIN));
    const { router } = renderAt(PATH);

    expect(await screen.findByText(ADMIN.email)).toBeInTheDocument();
    await nameAndSubmit();

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/worklist");
    expect(sentBody(fetchStub, "POST /auth/google/signup")).toEqual({
      account_name: "Made-up Clinic",
    });
  });

  it("asks for a name before sending anything", async () => {
    const fetchStub = stubSignup(PENDING);
    renderAt(PATH);

    await nameAndSubmit("");

    expect(await screen.findByText("Enter the account name.")).toBeInTheDocument();
    expect(screen.getByLabelText("Account name")).toHaveFocus();
    expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("says what is taken on a 409 and keeps the form for another name", async () => {
    stubSignup(PENDING, () => jsonResponse(409, { detail: "the account name is taken" }));
    renderAt(PATH);

    await nameAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent("The account name is taken.");
    expect(screen.getByLabelText("Account name")).toHaveValue("Made-up Clinic");
  });

  it("says the sign-up has expired when it opens without one", async () => {
    stubSignup(EXPIRED);
    renderAt(PATH);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Google sign-up has expired. Continue with Google again.",
    );
    expect(screen.queryByLabelText("Account name")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to log in" })).toHaveAttribute(
      "href",
      "/app/login",
    );
  });

  it("says the sign-up has expired when it runs out before the name is sent", async () => {
    stubSignup(PENDING, EXPIRED);
    renderAt(PATH);

    await nameAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent("The Google sign-up has expired.");
    expect(screen.queryByLabelText("Account name")).not.toBeInTheDocument();
  });

  it("says when the pending sign-up cannot be read", async () => {
    stubSignup(() => jsonResponse(500, {}));
    renderAt(PATH);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server. Try again later.",
    );
  });
});
