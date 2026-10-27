import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { checkGoogleSignup, focusFirstError } from "../auth/credentialCheck";
import type { FieldErrors, GoogleSignupField } from "../auth/credentialCheck";
import { GOOGLE_SIGNUP_EXPIRED } from "../auth/googleSignIn";
import { HOME_PATH } from "../auth/redirect";
import { useGoogleSignup, useGoogleSignupInfo } from "../auth/session";
import { AuthField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { asSentence, describeError } from "./errorMessage";
import { BuildingIcon } from "./icons";

function Expired() {
  return (
    <p className="error" role="alert">
      {GOOGLE_SIGNUP_EXPIRED} <Link to="/app/login">Back to log in</Link>
    </p>
  );
}

// A 409 says what is taken (the name, the email, or the Google account); that text is the API's.
function signupError(error: unknown): string {
  if (error instanceof ApiError && error.kind === "conflict") {
    return asSentence(error.message);
  }
  return describeError(error);
}

// The last step of a first "Continue with Google": the API knows the verified Google email (from
// its own signed cookie) and asks for the name of the new account, whose admin the user becomes.
export function GoogleNameAccountScreen() {
  const [accountName, setAccountName] = useState("");
  const [errors, setErrors] = useState<FieldErrors<GoogleSignupField>>({});
  const info = useGoogleSignupInfo();
  const signupMutation = useGoogleSignup();
  const navigate = useNavigate();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkGoogleSignup({ accountName });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    signupMutation.mutate(
      { account_name: accountName },
      { onSuccess: () => void navigate(HOME_PATH, { replace: true }) },
    );
  }

  // The cookie can also run out between opening the screen and sending the name.
  const expiredOnSend =
    signupMutation.error instanceof ApiError && signupMutation.error.kind === "unauthorized";

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
          Signing up as <strong>{info.data.email}</strong>
        </p>
        <form className="form auth-form" noValidate onSubmit={onSubmit}>
          <AuthField
            label="Account name"
            icon={<BuildingIcon />}
            name="accountName"
            type="text"
            autoComplete="organization"
            required
            maxLength={200}
            value={accountName}
            error={errors.accountName}
            onChange={(event) => setAccountName(event.target.value)}
          />
          {signupMutation.isError && (
            <p className="error" role="alert">
              {signupError(signupMutation.error)}
            </p>
          )}
          <button type="submit" className="auth-submit" disabled={signupMutation.isPending}>
            {signupMutation.isPending && <span className="spinner" aria-hidden="true" />}
            {signupMutation.isPending ? "Creating…" : "Create the account"}
          </button>
        </form>
      </>
    );
  }

  return (
    <AuthFrame
      title="Name your account"
      intro="Creates a new account; you become its admin and sign in with Google."
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
