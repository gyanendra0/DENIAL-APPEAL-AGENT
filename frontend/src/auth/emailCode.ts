// The code screen that confirms a new sign-up's email, and how a login tells the screens to go there.

import { ApiError } from "../api/client";

export const VERIFY_EMAIL_PATH = "/app/verify-email";

// The API's name for a login with the right password whose email is not confirmed yet. That answer
// has set the code screen's cookie and emailed a new code.
export const EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED";

export function isEmailNotVerified(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.code === EMAIL_NOT_VERIFIED;
}

// "Wait 1 second" or "Wait 42 seconds"; a minute or more is told in whole minutes, rounded up.
export function waitSentence(seconds: number): string {
  if (seconds >= 60) {
    const minutes = Math.ceil(seconds / 60);
    return `Wait ${minutes} ${minutes === 1 ? "minute" : "minutes"} before asking for another code.`;
  }
  return `Wait ${seconds} ${seconds === 1 ? "second" : "seconds"} before asking for another code.`;
}
