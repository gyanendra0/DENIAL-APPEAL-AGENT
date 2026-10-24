import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";

import { canUpload } from "../auth/roles";
import { useLogout, useSession } from "../auth/session";
import { describeError } from "./errorMessage";
import {
  ChartIcon,
  CloseIcon,
  ListIcon,
  LogOutIcon,
  LogoIcon,
  MenuIcon,
  PlusIcon,
  SidebarIcon,
} from "./icons";
import { RecentDenials } from "./RecentDenials";
import { readSidebarCollapsed, writeSidebarCollapsed } from "./sidebarPreference";

const SIDEBAR_ID = "app-sidebar";

function Brand() {
  return (
    <strong className="app-name">
      <span className="logo-tile">
        <LogoIcon />
      </span>
      <span className="app-name-text">Denial appeal agent</span>
    </strong>
  );
}

// The frame of every logged-in screen. It sits inside the guard, so the session is there.
// On a wide screen the sidebar collapses to icons; under 760 pixels it is a drawer opened from
// the bar on top (styles.css decides which of the two buttons shows).
export function AppLayout() {
  const session = useSession();
  const logoutMutation = useLogout();
  const navigate = useNavigate();
  const user = session.data;
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  // The focus goes into the drawer when it opens.
  useEffect(() => {
    if (drawerOpen) {
      closeButton.current?.focus();
    }
  }, [drawerOpen]);

  function onLogout() {
    logoutMutation.mutate(undefined, {
      onSuccess: () => void navigate("/app/login", { replace: true }),
    });
  }

  function toggleCollapsed() {
    writeSidebarCollapsed(!collapsed);
    setCollapsed(!collapsed);
  }

  function closeDrawer() {
    setDrawerOpen(false);
    menuButton.current?.focus();
  }

  function onSidebarKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (drawerOpen && event.key === "Escape") {
      closeDrawer();
    }
  }

  // Following a link in the drawer closes it, so the new screen is in view.
  function onSidebarClick(event: MouseEvent<HTMLElement>) {
    if (drawerOpen && event.target instanceof Element && event.target.closest("a") !== null) {
      setDrawerOpen(false);
    }
  }

  const shellClass = [
    "app-shell",
    collapsed ? "is-collapsed" : "",
    drawerOpen ? "drawer-open" : "",
  ].join(" ");

  return (
    <div className={shellClass.trim()}>
      <div className="app-topbar">
        <button
          ref={menuButton}
          type="button"
          className="icon-button"
          aria-label="Menu"
          aria-expanded={drawerOpen}
          aria-controls={SIDEBAR_ID}
          onClick={() => setDrawerOpen(true)}
        >
          <MenuIcon />
        </button>
        <Brand />
      </div>
      <header
        id={SIDEBAR_ID}
        className="app-header"
        onKeyDown={onSidebarKeyDown}
        onClick={onSidebarClick}
      >
        <div className="app-sidebar-top">
          <Brand />
          <button
            type="button"
            className="icon-button app-collapse"
            aria-label="Sidebar"
            aria-expanded={!collapsed}
            aria-controls={SIDEBAR_ID}
            onClick={toggleCollapsed}
          >
            <SidebarIcon />
          </button>
          <button
            ref={closeButton}
            type="button"
            className="icon-button app-drawer-close"
            aria-label="Close menu"
            onClick={closeDrawer}
          >
            <CloseIcon />
          </button>
        </div>
        {canUpload(user?.role) && (
          <NavLink to="/app/upload" className="new-upload">
            <PlusIcon />
            <span className="nav-text">Upload</span>
          </NavLink>
        )}
        <nav className="app-nav">
          <NavLink to="/app/worklist">
            <ListIcon />
            <span className="nav-text">Worklist</span>
          </NavLink>
          <NavLink to="/app/overview">
            <ChartIcon />
            <span className="nav-text">Overview</span>
          </NavLink>
        </nav>
        <RecentDenials />
        <div className="app-account">
          {user && (
            <span className="muted app-account-text">
              {user.account_name} · {user.email} ({user.role})
            </span>
          )}
          <button
            type="button"
            className="secondary app-logout"
            onClick={onLogout}
            disabled={logoutMutation.isPending}
          >
            <LogOutIcon />
            <span className="nav-text">Log out</span>
          </button>
        </div>
      </header>
      {drawerOpen && <div className="app-backdrop" aria-hidden="true" onClick={closeDrawer} />}
      <div className="app-main">
        {logoutMutation.isError && (
          <p className="page error" role="alert">
            {describeError(logoutMutation.error)}
          </p>
        )}
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
