import { Link, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { GOOGLE_SIGNUP_EXPIRED } from "../auth/googleSignIn";
import { HOME_PATH } from "../auth/redirect";
import { useGoogleSignup, useGoogleSignupInfo } from "../auth/session";
import { AuthFrame } from "./AuthFrame";
import { asSentence, describeError } from "./errorMessage";

function Expired() {
  return (
    <p className="error" role="alert">
      {GOOGLE_SIGNUP_EXPIRED} <Link to="/app/login">Back to log in</Link>
    </p>
  );
}

// A 409 says what is taken (the email, or the Google account) and a 403 that sign-up is no longer
// open for this email; that text is the API's.
function signupError(error: unknown): string {
  if (error instanceof ApiError && (error.kind === "conflict" || error.kind === "forbidden")) {
    return asSentence(error.message);
  }
  return describeError(error);
}

// The last step of a first "Continue with Google": the API knows the verified Google email (from
// its own signed cookie) and the account: the shared one, or a new personal one for an individual.
// One deliberate click joins it, so a Google click alone never creates a user.
export function GoogleJoinScreen() {
  const info = useGoogleSignupInfo();
  const signupMutation = useGoogleSignup();
  const navigate = useNavigate();

  function join() {
    signupMutation.mutate(undefined, {
      onSuccess: () => void navigate(HOME_PATH, { replace: true }),
    });
  }

  // The cookie can also run out between opening the screen and the click.
  const expiredOnSend =
    signupMutation.error instanceof ApiError && signupMutation.error.kind === "unauthorized";

  const personal = info.data?.account_kind === "personal";
  const label = personal ? "Create" : "Join";
  const pendingLabel = personal ? "Creating…" : "Joining…";

  let body;
  if (info.isPending) {
    body = <p>Loading…</p>;
  } else if (info.isError) {
    body = (
      <p className="error" role="alert">
        {describeError(info.error)}
      </p>
    );
  } else if (info.data === null || expiredOnSend) {
    body = <Expired />;
  } else {
    body = (
      <>
        <p className="auth-signing-as">
          {personal ? (
            "Create your personal space"
          ) : (
            <>
              Join <strong>{info.data.account_name}</strong>
            </>
          )}{" "}
          as <strong>{info.data.email}</strong>?
        </p>
        {signupMutation.isError && (
          <p className="error" role="alert">
            {signupError(signupMutation.error)}
          </p>
        )}
        <button
          type="button"
          className="auth-submit"
          disabled={signupMutation.isPending}
          onClick={join}
        >
          {signupMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {signupMutation.isPending ? pendingLabel : label}
        </button>
      </>
    );
  }

  return (
    <AuthFrame
      title={personal ? "Create your personal space" : "Join the account"}
      intro={
        personal
          ? "You get a personal space of your own and sign in with Google."
          : "You join your organisation's account and sign in with Google."
      }
      footer={
        <>
          Have an account? <Link to="/app/login">Log in</Link>
        </>
      }
    >
      {body}
    </AuthFrame>
  );
}
