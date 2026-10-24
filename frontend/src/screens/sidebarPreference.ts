// Whether the sidebar is collapsed, remembered in this browser only. Storage can be missing or
// refuse (a private window, blocked site data); the sidebar then simply starts expanded.

const SIDEBAR_COLLAPSED_KEY = "sidebar-collapsed";

export function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeSidebarCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
  } catch {
    // Not remembered; the sidebar still collapses for this visit.
  }
}
