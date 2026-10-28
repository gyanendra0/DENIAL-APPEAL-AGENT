import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkSignup,
  focusFirstError,
} from "../auth/credentialCheck";
import type { FieldErrors, SignupField } from "../auth/credentialCheck";
import { VERIFY_EMAIL_PATH } from "../auth/emailCode";
import { useSignup } from "../auth/session";
import { AuthField, PasswordField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { asSentence, describeError } from "./errorMessage";
import { GoogleButton } from "./GoogleButton";
import { MailIcon } from "./icons";

// A 409 says what is taken ("the email is taken") and a 403 that sign-up is not open for this email;
// that text comes from the API, not the user. A 503 means the daily email cap is reached: nothing
// was created.
function signupError(error: unknown): string {
  if (error instanceof ApiError && (error.kind === "conflict" || error.kind === "forbidden")) {
    return asSentence(error.message);
  }
  return describeError(error, { 503: "We cannot send email right now. Try again later." });
}

export function SignupScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors<SignupField>>({});
  const signupMutation = useSignup();
  const navigate = useNavigate();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkSignup({ email, password });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    signupMutation.mutate(
      { email, password },
      // The user exists but nobody is logged in until the emailed code is typed.
      { onSuccess: () => void navigate(VERIFY_EMAIL_PATH, { replace: true }) },
    );
  }

  return (
    <AuthFrame
      title="Sign up"
      intro="Joins your organisation's account. We email you a code to confirm it."
      footer={
        <>
          Have an account? <Link to="/app/login">Log in</Link>
        </>
      }
    >
      <GoogleButton />
      <form className="form auth-form" noValidate onSubmit={onSubmit}>
        <AuthField
          label="Email"
          icon={<MailIcon />}
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          error={errors.email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <PasswordField
          name="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_CHARACTERS}
          maxLength={MAX_PASSWORD_CHARACTERS}
          hint={`${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`}
          value={password}
          error={errors.password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {signupMutation.isError && (
          <p className="error" role="alert">
            {signupError(signupMutation.error)}
          </p>
        )}
        <button type="submit" className="auth-submit" disabled={signupMutation.isPending}>
          {signupMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {signupMutation.isPending ? "Signing up…" : "Sign up"}
        </button>
      </form>
    </AuthFrame>
  );
}
