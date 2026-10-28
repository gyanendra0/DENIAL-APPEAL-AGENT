import type { UserRole } from "../api/types";

// The same role lists as `src/api/roles.py`. The UI hides what a role cannot do; the API decides
// too (403).

// Who may upload a denial letter: everyone. Each user sees only the letters they uploaded, an admin
// sees every letter of the account.
const UPLOAD_ROLES: readonly UserRole[] = ["viewer", "reviewer", "admin"];

export function canUpload(role: UserRole | undefined): boolean {
  return role !== undefined && UPLOAD_ROLES.includes(role);
}

// Who may edit a draft. A viewer reads it only.
const EDIT_ROLES: readonly UserRole[] = ["reviewer", "admin"];

export function canEdit(role: UserRole | undefined): boolean {
  return role !== undefined && EDIT_ROLES.includes(role);
}

// Who may approve or reject a draft: admins only.
const DECIDE_ROLES: readonly UserRole[] = ["admin"];

export function canDecide(role: UserRole | undefined): boolean {
  return role !== undefined && DECIDE_ROLES.includes(role);
}
