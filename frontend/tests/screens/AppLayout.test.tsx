import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_KEY } from "../../src/auth/session";
import { ADMIN, jsonResponse, renderAt, stubApi } from "../helpers";

describe("AppLayout", () => {
  it("shows the account, the email and the role", async () => {
    stubApi({ "GET /me": () => jsonResponse(200, ADMIN) });

    renderAt("/app/worklist");

    expect(
      await screen.findByText("Made-up Clinic · ada@example.test (admin)"),
    ).toBeInTheDocument();
  });

  it("logs out, empties the cache and goes to login", async () => {
    const fetchStub = stubApi({
      "GET /me": () => jsonResponse(200, ADMIN),
      "POST /auth/logout": () => new Response(null, { status: 204 }),
    });
    const { router, queryClient } = renderAt("/app/worklist");
    queryClient.setQueryData(["worklist"], { items: [], next_before: null });

    await userEvent.setup().click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
    expect(queryClient.getQueryData(["worklist"])).toBeUndefined();
    expect(queryClient.getQueryData(SESSION_KEY)).toBeNull();
    expect(fetchStub.mock.calls.filter(([path]) => path === "/me")).toHaveLength(1);
  });

  it("shows an error and stays when logout fails", async () => {
    stubApi({
      "GET /me": () => jsonResponse(200, ADMIN),
      "POST /auth/logout": () => Promise.reject(new TypeError("offline")),
    });
    const { router } = renderAt("/app/worklist");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
    expect(router.state.location.pathname).toBe("/app/worklist");
  });
});
