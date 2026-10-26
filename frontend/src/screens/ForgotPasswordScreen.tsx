import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";

import { checkResetAsk, focusFirstError } from "../auth/credentialCheck";
import type { FieldErrors, ResetAskField } from "../auth/credentialCheck";
import { useAskPasswordReset } from "../auth/session";
import { AuthField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { describeError } from "./errorMessage";
import { MailIcon } from "./icons";

// Asks for a reset link. The API answers the same for an email with and without an account, so the
// screen does too and never says whether the email is known.
export function ForgotPasswordScreen() {
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<FieldErrors<ResetAskField>>({});
  const askMutation = useAskPasswordReset();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkResetAsk({ email });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    askMutation.mutate({ email });
  }

  return (
    <AuthFrame
      title="Reset your password"
      intro="Enter your email and we send you a link to set a new password."
      footer={
        <>
          Remembered it? <Link to="/app/login">Log in</Link>
        </>
      }
    >
      {askMutation.isSuccess ? (
        <p role="status">
          If the email belongs to an account, a link is on its way. Check your inbox.
        </p>
      ) : (
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
          {askMutation.isError && (
            <p className="error" role="alert">
              {describeError(askMutation.error)}
            </p>
          )}
          <button type="submit" className="auth-submit" disabled={askMutation.isPending}>
            {askMutation.isPending && <span className="spinner" aria-hidden="true" />}
            {askMutation.isPending ? "Sending…" : "Send the link"}
          </button>
        </form>
      )}
    </AuthFrame>
  );
}
