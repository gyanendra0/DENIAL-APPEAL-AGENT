import type { UserRole } from "../api/types";

// Who may upload a denial letter. The UI hides the upload from anyone else; the API decides too
// (403).
const UPLOAD_ROLES: readonly UserRole[] = ["reviewer", "admin"];

export function canUpload(role: UserRole | undefined): boolean {
  return role !== undefined && UPLOAD_ROLES.includes(role);
}

// Who may edit, approve or reject a draft (the API's `REVIEW_ROLES`). A viewer reads it only; the
// API decides too (403).
const REVIEW_ROLES: readonly UserRole[] = ["reviewer", "admin"];

export function canReview(role: UserRole | undefined): boolean {
  return role !== undefined && REVIEW_ROLES.includes(role);
}
