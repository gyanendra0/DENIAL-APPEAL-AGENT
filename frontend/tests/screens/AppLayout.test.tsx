import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SESSION_KEY } from "../../src/auth/session";
import { worklistKey } from "../../src/worklist/useWorklist";
import {
  EMPTY_WORKLIST,
  ADMIN,
  INDIVIDUAL,
  REVIEWER,
  VIEWER,
  jsonResponse,
  notifications,
  renderAt,
  stubApi,
} from "../helpers";

// The account menu's button is named by the user's first name, made from the email.
async function openAccountMenu(name = "Ada") {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name }));
  return user;
}

describe("AppLayout", () => {
  it("shows the first name with its initial and keeps the menu closed", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });

    renderAt("/app/worklist");

    const button = await screen.findByRole("button", { name: "Ada" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button.querySelector(".avatar")).toHaveTextContent("A");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByText("ada@example.test")).not.toBeInTheDocument();
  });

  it("opens the menu with the email, account and role, and the focus on the first item", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");

    await openAccountMenu();

    const menu = screen.getByRole("menu", { name: "Ada" });
    expect(within(menu).getByText("ada@example.test")).toBeInTheDocument();
    expect(within(menu).getByText("Made-up Clinic · admin")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ada" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitem", { name: "Change password" })).toHaveFocus();
    expect(screen.getByRole("menuitem", { name: "Log out" })).toBeInTheDocument();
  });

  it("offers Delete account to a team member never", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");

    await openAccountMenu();

    expect(screen.queryByRole("menuitem", { name: "Delete account" })).not.toBeInTheDocument();
  });

  it("shows Personal for an individual and never the made-up account name", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, INDIVIDUAL) });
    renderAt("/app/worklist");

    await openAccountMenu("Lin");

    expect(screen.getByText("Personal · admin")).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(INDIVIDUAL.account_name))).not.toBeInTheDocument();
  });

  it("opens the delete step for an individual from the menu", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, INDIVIDUAL) });
    const { router } = renderAt("/app/worklist");
    const user = await openAccountMenu("Lin");

    await user.click(screen.getByRole("menuitem", { name: "Delete account" }));

    expect(await screen.findByRole("button", { name: "Delete for good" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/account");
    expect(router.state.location.search).toBe("?open=delete");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes the menu with Escape and gives the focus back to its button", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = await openAccountMenu();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ada" })).toHaveFocus();
  });

  it("closes the menu on a click outside it, and on a second click of its button", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = await openAccountMenu();

    await user.click(screen.getByRole("heading", { name: "Worklist" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ada" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ada" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes the menu when Tab moves the focus out of it, not while it stays inside", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = await openAccountMenu();

    await user.tab();
    expect(screen.getByRole("menuitem", { name: "Log out" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Ada" })).toHaveFocus();
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.tab();

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ada" })).toHaveAttribute("aria-expanded", "false");
  });

  it("moves between the menu items with the arrow keys, round the ends", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = await openAccountMenu();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Log out" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Change password" })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Log out" })).toHaveFocus();
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

    const user = await openAccountMenu();
    await user.click(screen.getByRole("menuitem", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/login");
    expect(notifications().getByText("You're logged out")).toBeInTheDocument();
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

    const user = await openAccountMenu();
    await user.click(screen.getByRole("menuitem", { name: "Log out" }));

    expect(await notifications().findByText("Logging out failed")).toBeInTheDocument();
    expect(notifications().getByText(/The API could not be reached/)).toBeInTheDocument();
    expect(screen.getByLabelText("Notifications").firstElementChild).toHaveAttribute(
      "aria-live",
      "assertive",
    );
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

  it("links to the upload screen for a viewer too", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, VIEWER) });

    renderAt("/app/worklist");

    expect(await screen.findByRole("link", { name: "Worklist" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload" })).toHaveAttribute("href", "/app/upload");
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/app/overview");
    expect(screen.queryByRole("link", { name: "Account" })).not.toBeInTheDocument();
  });

  it("opens the change password form from the menu for a viewer", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, VIEWER) });
    const { router } = renderAt("/app/worklist");
    const user = await openAccountMenu();

    await user.click(screen.getByRole("menuitem", { name: "Change password" }));

    expect(await screen.findByLabelText(/^Current password/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/account");
    expect(router.state.location.search).toBe("?open=password");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
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

  it("closes only the account menu, not the drawer around it, with Escape", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("button", { name: "Menu" });
    await user.click(drawer);
    await user.click(screen.getByRole("button", { name: "Ada" }));

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(drawer).toHaveAttribute("aria-expanded", "true");
  });

  it("still closes the drawer with Escape while the account menu is closed", async () => {
    stubApi({ ...EMPTY_WORKLIST, "GET /me": () => jsonResponse(200, ADMIN) });
    renderAt("/app/worklist");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("button", { name: "Menu" });
    await user.click(drawer);
    screen.getByRole("button", { name: "Ada" }).focus();

    await user.keyboard("{Escape}");

    expect(drawer).toHaveAttribute("aria-expanded", "false");
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
