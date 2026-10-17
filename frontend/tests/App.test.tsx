import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "../src/router";
import { ADMIN, EMPTY_WORKLIST, jsonResponse, renderAt, stubApi } from "./helpers";

describe("App", () => {
  it("sends the root path through the worklist to login when there is no session", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(401, { detail: "not logged in" }) });

    const { router } = renderAt("/");

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.state).toEqual({ from: "/app/worklist" });
  });

  it.each(["/app", "/somewhere/else"])("sends %s to the worklist", async (path) => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });

    const { router } = renderAt(path);

    expect(await screen.findByRole("heading", { name: "Worklist" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/worklist");
  });

  it("builds a browser router at the page's own path", () => {
    window.history.replaceState(null, "", "/app/login");

    expect(createAppRouter().state.location.pathname).toBe("/app/login");
  });
});
