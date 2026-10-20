export const HOME_PATH = "/app/worklist";

// Where to go after login: the page the guard remembered, if it is one of the app's own screens
// and was remembered for this user (`userId`). Anything else (no state, another site, the login or
// sign-up screen itself, a page another user had open) goes home. A state without `userId` comes
// from a page load without a session, so no earlier user is known and the page is kept.
export function afterLoginPath(state: unknown, userId: number): string {
  if (typeof state !== "object" || state === null || !("from" in state)) {
    return HOME_PATH;
  }
  if ("userId" in state && state.userId !== userId) {
    return HOME_PATH;
  }
  const from = state.from;
  if (typeof from !== "string" || !from.startsWith("/app/")) {
    return HOME_PATH;
  }
  if (from.startsWith("/app/login") || from.startsWith("/app/signup")) {
    return HOME_PATH;
  }
  return from;
}
