export const HOME_PATH = "/app/worklist";

// Where to go after login: the page the guard remembered, if it is one of the app's own screens.
// Anything else (no state, another site, the login or sign-up screen itself) goes home.
export function afterLoginPath(state: unknown): string {
  if (typeof state !== "object" || state === null || !("from" in state)) {
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
