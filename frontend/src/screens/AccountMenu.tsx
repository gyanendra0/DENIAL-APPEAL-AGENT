import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link } from "react-router";

import type { UserResponse } from "../api/types";
import { accountFormPath } from "./accountPaths";
import { firstNameFromEmail } from "./firstName";
import { ChevronUpIcon, LockIcon, LogOutIcon, TrashIcon } from "./icons";

const MENU_ID = "account-menu";

// A personal account's name is made up (`personal-…`), so the screen names its kind instead.
function accountLabel(user: UserResponse): string {
  return user.account_kind === "personal" ? "Personal" : user.account_name;
}

interface AccountMenuProps {
  user: UserResponse;
  loggingOut: boolean;
  onLogout: () => void;
}

// The user at the bottom of the sidebar: a round initial and the first name. A click opens a menu
// above it. Escape, a click outside or choosing an item closes it; the arrow keys move between the
// items. Only a personal account can be deleted (the API refuses the others), so only it offers it.
export function AccountMenu({ user, loggingOut, onLogout }: AccountMenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const name = firstNameFromEmail(user.email);

  function menuItems(): HTMLElement[] {
    return Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
  }

  // The focus goes to the first item when the menu opens.
  useEffect(() => {
    if (open) {
      menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    }
  }, [open]);

  // A click anywhere outside the menu and its button closes it.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    function onMouseDown(event: MouseEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  function close() {
    setOpen(false);
    toggle.current?.focus();
  }

  // Escape closes only the menu, not the drawer around it, so it stops here.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!open) {
      return;
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const items = menuItems();
      const at = items.findIndex((item) => item === document.activeElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  }

  return (
    <div ref={root} className="account-menu" onKeyDown={onKeyDown}>
      {open && (
        <div ref={menu} id={MENU_ID} className="account-menu-list" role="menu" aria-label={name}>
          <p className="account-menu-who">
            <span>{user.email}</span>
            <span className="muted">
              {accountLabel(user)} · {user.role}
            </span>
          </p>
          <Link role="menuitem" to={accountFormPath("password")} onClick={() => setOpen(false)}>
            <LockIcon />
            Change password
          </Link>
          {user.account_kind === "personal" && (
            <Link role="menuitem" to={accountFormPath("delete")} onClick={() => setOpen(false)}>
              <TrashIcon />
              Delete account
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            disabled={loggingOut}
            onClick={() => {
              setOpen(false);
              onLogout();
            }}
          >
            <LogOutIcon />
            Log out
          </button>
        </div>
      )}
      <button
        ref={toggle}
        type="button"
        className="account-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? MENU_ID : undefined}
        onClick={() => setOpen(!open)}
      >
        <span className="avatar" aria-hidden="true">
          {name.charAt(0)}
        </span>
        <span className="nav-text account-menu-name">{name}</span>
        <span className="nav-text account-menu-chevron">
          <ChevronUpIcon />
        </span>
      </button>
    </div>
  );
}
