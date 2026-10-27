// The path of the "Forgot password?" screen and the note it leaves for the login screen once the
// new password is set.

export const FORGOT_PASSWORD_PATH = "/app/forgot-password";

// What the login screen is sent with after a reset, so it can say the password is set.
export const PASSWORD_SET_STATE = { passwordSet: true } as const;

export function isPasswordSetState(state: unknown): boolean {
  return (
    typeof state === "object" &&
    state !== null &&
    "passwordSet" in state &&
    state.passwordSet === true
  );
}
