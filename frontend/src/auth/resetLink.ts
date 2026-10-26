// The paths of the password reset screens, the token in the emailed link, and the note the reset
// screen leaves for the login screen.

export const FORGOT_PASSWORD_PATH = "/app/forgot-password";
export const RESET_PASSWORD_PATH = "/app/reset-password";

// What the login screen is sent with after a reset, so it can say the password is set.
export const PASSWORD_SET_STATE = { passwordSet: true } as const;

// The emailed link is `/app/reset-password#token=...`; the fragment is never sent to a server.
// An empty or missing token is null.
export function tokenFromHash(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return token === null || token === "" ? null : token;
}

export function isPasswordSetState(state: unknown): boolean {
  return (
    typeof state === "object" &&
    state !== null &&
    "passwordSet" in state &&
    state.passwordSet === true
  );
}
