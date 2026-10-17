import type { UserRole } from "../api/types";

// Who may upload a denial letter. The UI hides the upload from anyone else; the API decides too
// (403).
const UPLOAD_ROLES: readonly UserRole[] = ["reviewer", "admin"];

export function canUpload(role: UserRole | undefined): boolean {
  return role !== undefined && UPLOAD_ROLES.includes(role);
}
