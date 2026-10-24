import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SESSION_KEY } from "../../src/auth/session";
import { worklistKey } from "../../src/worklist/useWorklist";
import {
  EMPTY_WORKLIST,
  ADMIN,
  REVIEWER,
  VIEWER,
  jsonResponse,
  renderAt,
  stubApi,
} from "../helpers";

describe("AppLayout", () => {
  it("shows the account, the email and the role", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });

    renderAt("/app/worklist");

    expect(
      await screen.findByText("Made-up Clinic · ada@example.test (admin)"),
    ).toBeInTheDocument();
  });

  it("logs out, empties the cache and goes to login", async () => {
    const fetchStub = stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      "POST /auth/logout": () => new Response(null, { status: 204 }),
    });
    const { router, queryClient } = renderAt("/app/worklist");
    const allJobs = worklistKey({ status: null, reviewStatus: null });
    await screen.findByText(/No jobs here yet/);
    expect(queryClient.getQueryData(allJobs)).toBeDefined();

    await userEvent.setup().click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
    expect(queryClient.getQueryData(allJobs)).toBeUndefined();
    expect(queryClient.getQueryData(SESSION_KEY)).toBeNull();
    expect(fetchStub.mock.calls.filter(([path]) => path === "/me")).toHaveLength(1);
  });

  it("shows an error and stays when logout fails", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      "POST /auth/logout": () => Promise.reject(new TypeError("offline")),
    });
    const { router } = renderAt("/app/worklist");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
    expect(router.state.location.pathname).toBe("/app/worklist");
  });

  it("links to the upload screen for a reviewer", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, REVIEWER) });

    renderAt("/app/worklist");

    expect(await screen.findByRole("link", { name: "Upload" })).toHaveAttribute(
      "href",
      "/app/upload",
    );
    expect(screen.getByRole("link", { name: "Worklist" })).toBeInTheDocument();
  });

  it("hides the upload link from a viewer", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, VIEWER) });

    renderAt("/app/worklist");

    expect(await screen.findByRole("link", { name: "Worklist" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Upload" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/app/overview");
  });

  it("collapses the sidebar and remembers it in this browser", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const toggle = await screen.findByRole("button", { name: "Sidebar" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem("sidebar-collapsed")).toBe("true");
    // Collapsed links keep their names for screen readers.
    expect(screen.getByRole("link", { name: "Worklist" })).toBeInTheDocument();
  });

  it("starts collapsed when this browser remembers it", async () => {
    window.localStorage.setItem("sidebar-collapsed", "true");
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");

    expect(await screen.findByRole("button", { name: "Sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("opens the menu drawer, moves the focus into it and closes it with Escape", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const menu = await screen.findByRole("button", { name: "Menu" });
    await user.click(menu);

    expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
  });

  it("makes the page behind the open drawer inert and closes it with Escape from anywhere", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const menu = await screen.findByRole("button", { name: "Menu" });
    const topbar = document.querySelector(".app-topbar");
    const main = document.querySelector(".app-main");
    expect(main).not.toHaveAttribute("inert");
    await user.click(menu);

    expect(topbar).toHaveAttribute("inert");
    expect(main).toHaveAttribute("inert");

    // The focus is outside the drawer, as if it had got past the last item.
    screen.getByRole("button", { name: "Close menu" }).blur();
    expect(document.body).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(topbar).not.toHaveAttribute("inert");
    expect(main).not.toHaveAttribute("inert");
    expect(menu).toHaveFocus();
  });

  it("closes the drawer when the window grows past the drawer width", async () => {
    const listeners: ((event: MediaQueryListEvent) => void)[] = [];
    const resize = (matches: boolean) =>
      act(() => listeners.forEach((listener) => listener({ matches } as MediaQueryListEvent)));
    vi.stubGlobal("matchMedia", (query: string) => ({
      media: query,
      matches: true,
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) =>
        listeners.push(listener),
      removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) =>
        listeners.splice(listeners.indexOf(listener), 1),
    }));
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const menu = await screen.findByRole("button", { name: "Menu" });
    await user.click(menu);
    // Still narrow (a change between two narrow widths): the drawer stays open.
    resize(true);
    expect(menu).toHaveAttribute("aria-expanded", "true");

    resize(false);

    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".app-main")).not.toHaveAttribute("inert");
    expect(listeners).toHaveLength(0);
  });

  it("closes the drawer when a link in it is followed", async () => {
    stubApi({
      ...EMPTY_WORKLIST,
      "GET /me": () => jsonResponse(200, ADMIN),
      "GET /overview": () => jsonResponse(500, { detail: "not needed here" }),
    });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const menu = await screen.findByRole("button", { name: "Menu" });
    await user.click(menu);
    await user.click(screen.getByRole("link", { name: "Overview" }));

    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it("closes the drawer with its close button and the backdrop", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();

    const menu = await screen.findByRole("button", { name: "Menu" });
    await user.click(menu);
    await user.click(screen.getByRole("button", { name: "Close menu" }));
    expect(menu).toHaveAttribute("aria-expanded", "false");

    await user.click(menu);
    const backdrop = document.querySelector(".app-backdrop");
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as Element);
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });
});
