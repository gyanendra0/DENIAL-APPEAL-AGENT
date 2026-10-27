import { GOOGLE_START_PATH } from "../api/auth";
import { useAuthOptions } from "../auth/session";
import { GoogleIcon } from "./icons";

// "Continue with Google" over the login and sign-up forms, only when the server offers it. While
// the answer is on its way, or if it fails, the email and password form is all there is.
// The button is a link, not a `fetch`: the browser itself goes to Google and comes back.
export function GoogleButton() {
  const options = useAuthOptions();
  if (options.data?.google !== true) {
    return null;
  }
  return (
    <>
      <a className="auth-google" href={GOOGLE_START_PATH}>
        <GoogleIcon />
        Continue with Google
      </a>
      <p className="auth-divider" aria-hidden="true">
        or
      </p>
    </>
  );
}
