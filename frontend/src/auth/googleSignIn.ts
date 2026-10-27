// The "Continue with Google" screen path and the sentences for the codes the API sends a refused
// Google sign-in back to the login screen with (`/app/login?google=<code>`, see
// src/api/google_identity.py GoogleRefusal). The screen never shows what Google sent.

// Where the API sends a Google account that has no user yet. The API names the same path.
export const GOOGLE_NAME_ACCOUNT_PATH = "/app/google/name-account";

// The pending sign-up's cookie is gone or older than ten minutes.
export const GOOGLE_SIGNUP_EXPIRED = "The Google sign-up has expired. Continue with Google again.";

// A Map, not an object, so a code such as "toString" finds nothing.
const MESSAGE_BY_REFUSAL: ReadonlyMap<string, string> = new Map([
  ["failed", "Google sign-in did not finish. Try again."],
  [
    "not_verified",
    "Google has not verified this email. Use another Google account or your password.",
  ],
  ["use_password", "This email already has a login here. Log in with your password."],
  [
    "use_signup",
    "Google can't confirm this email belongs to you. Sign up with your email and a password.",
  ],
  ["linked_elsewhere", "This Google account is linked to another login."],
  ["expired", "Google sign-in took too long, or was started again in another tab. Try again."],
]);

// The sentence for the `google` code in the address's query, or null when there is none. An
// unknown code is shown as nothing rather than as its text.
export function googleRefusalMessage(search: string): string | null {
  const code = new URLSearchParams(search).get("google");
  return code === null ? null : (MESSAGE_BY_REFUSAL.get(code) ?? null);
}
