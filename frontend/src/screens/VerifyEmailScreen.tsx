import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { checkCode, focusFirstError } from "../auth/credentialCheck";
import type { CodeField, FieldErrors } from "../auth/credentialCheck";
import { waitSentence } from "../auth/emailCode";
import { HOME_PATH } from "../auth/redirect";
import { useResendCode, useVerifyEmail } from "../auth/session";
import { AuthField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { describeError } from "./errorMessage";
import { LockIcon } from "./icons";

const CODE_NOT_VALID = "The code is wrong or has expired.";
const SCREEN_EXPIRED = "This code screen has expired. Log in again to get a new code.";
const MAIL_UNAVAILABLE = "We cannot send email right now. Try again later.";

function isExpired(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "unauthorized";
}

// A 429 from the resend says how long to wait; without a wait it is told like any other 429.
function resendError(error: unknown): string {
  if (error instanceof ApiError && error.kind === "too_many" && error.retryAfter !== null) {
    return waitSentence(error.retryAfter);
  }
  return describeError(error, {
    401: SCREEN_EXPIRED,
    409: "Your email is already confirmed.",
    503: MAIL_UNAVAILABLE,
  });
}

// Confirms a new sign-up's email with the 6-digit code it was sent, and logs in. The API knows the
// user from its own signed cookie (set by sign-up and by a login whose email is not confirmed), so
// the screen never holds or shows the email. A reload keeps working while the cookie lasts.
export function VerifyEmailScreen() {
  const [code, setCode] = useState("");
  const [errors, setErrors] = useState<FieldErrors<CodeField>>({});
  const verifyMutation = useVerifyEmail();
  const resendMutation = useResendCode();
  const navigate = useNavigate();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkCode({ code });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    verifyMutation.mutate(
      { code: code.trim() },
      { onSuccess: () => void navigate(HOME_PATH, { replace: true }) },
    );
  }

  const expired = isExpired(verifyMutation.error) || isExpired(resendMutation.error);
  const loginLink = <Link to="/app/login">Log in</Link>;

  return (
    <AuthFrame
      title="Confirm your email"
      intro="We sent a 6-digit code to your email. Type it here to confirm it and log in."
      footer={
        <>
          Wrong email? <Link to="/app/signup">Sign up again</Link>
        </>
      }
    >
      <form className="form auth-form" noValidate onSubmit={onSubmit}>
        <AuthField
          label="Code"
          icon={<LockIcon />}
          name="code"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          maxLength={6}
          value={code}
          error={errors.code}
          onChange={(event) => setCode(event.target.value)}
        />
        {verifyMutation.isError && (
          <p className="error" role="alert">
            {describeError(verifyMutation.error, { 400: CODE_NOT_VALID, 401: SCREEN_EXPIRED })}
            {isExpired(verifyMutation.error) && <> {loginLink}</>}
          </p>
        )}
        <button type="submit" className="auth-submit" disabled={verifyMutation.isPending}>
          {verifyMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {verifyMutation.isPending ? "Confirming…" : "Confirm"}
        </button>
      </form>
      <div className="form auth-form">
        <button
          type="button"
          className="secondary"
          disabled={resendMutation.isPending || expired}
          onClick={() => resendMutation.mutate()}
        >
          {resendMutation.isPending ? "Sending…" : "Send a new code"}
        </button>
        {resendMutation.isSuccess && <p role="status">A new code is on its way.</p>}
        {resendMutation.isError && (
          <p className="error" role="alert">
            {resendError(resendMutation.error)}
            {resendMutation.error instanceof ApiError &&
              (resendMutation.error.kind === "unauthorized" ||
                resendMutation.error.kind === "conflict") && <> {loginLink}</>}
          </p>
        )}
      </div>
    </AuthFrame>
  );
}
