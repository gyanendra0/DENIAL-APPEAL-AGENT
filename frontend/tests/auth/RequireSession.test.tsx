import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, stubApi, NO_RECENT } from "../helpers";

describe("RequireSession", () => {
  it("shows the screen to a logged-in user", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });

    renderAt("/app/worklist");

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
  });

  it("sends a user without a session to login and remembers the page", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(401, { detail: "not logged in" }) });

    const { router } = renderAt("/app/worklist?status=done");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
    expect(router.state.location.state).toEqual({ from: "/app/worklist?status=done" });
  });

  it("remembers who was logged in when the session ends", async () => {
    stubApi({
      ...NO_RECENT,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /worklist?limit=50": () => jsonResponse(401, { detail: "not logged in" }),
    });

    const { router } = renderAt("/app/worklist");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.state).toEqual({ from: "/app/worklist", userId: ADMIN.user_id });
  });

  it("shows a loading line while the session is asked for", () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => new Promise<Response>(() => undefined) });

    renderAt("/app/worklist");

    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error when the API cannot be reached", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => Promise.reject(new TypeError("offline")) });

    renderAt("/app/worklist");

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
  });
});
