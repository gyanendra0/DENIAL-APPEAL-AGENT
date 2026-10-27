import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkResetAsk,
  checkResetConfirm,
  focusFirstError,
} from "../auth/credentialCheck";
import type { FieldErrors, ResetAskField, ResetConfirmField } from "../auth/credentialCheck";
import { PASSWORD_SET_STATE } from "../auth/passwordReset";
import { useAskPasswordReset, useConfirmPasswordReset } from "../auth/session";
import { AuthField, PasswordField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { describeError } from "./errorMessage";
import { LockIcon, MailIcon } from "./icons";

// The API's 400 is one sentence for every problem: an unknown email, a wrong, expired or replaced
// code, or a code out of tries. So the screen never says whether the email has an account.
const CODE_NOT_VALID = "The code is wrong or has expired.";

// Two steps on one screen: the email asks for a code, then the code and a new password set it. The
// API answers the ask the same for an email with and without an account, so the screen does too.
// The email is kept in this screen's state only, never in the address bar; a reload starts again.
// The API does not log in, so a success goes to the login screen.
export function ForgotPasswordScreen() {
  const [email, setEmail] = useState("");
  // Set once the first ask is answered; asking for a new code stays on the second step.
  const [codeSent, setCodeSent] = useState(false);
  const [resent, setResent] = useState(false);
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [askErrors, setAskErrors] = useState<FieldErrors<ResetAskField>>({});
  const [confirmErrors, setConfirmErrors] = useState<FieldErrors<ResetConfirmField>>({});
  const askMutation = useAskPasswordReset();
  const confirmMutation = useConfirmPasswordReset();
  const navigate = useNavigate();

  function onAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkResetAsk({ email });
    setAskErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    askMutation.mutate({ email }, { onSuccess: () => setCodeSent(true) });
  }

  // The answer is the same whether or not a code was sent (the hourly cap is silent too).
  function onResend() {
    setResent(false);
    askMutation.mutate({ email }, { onSuccess: () => setResent(true) });
  }

  function onConfirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkResetConfirm({ code, newPassword });
    setConfirmErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    confirmMutation.mutate(
      { email, code: code.trim(), new_password: newPassword },
      {
        onSuccess: () => {
          void navigate("/app/login", { replace: true, state: PASSWORD_SET_STATE });
        },
      },
    );
  }

  return (
    <AuthFrame
      title="Reset your password"
      intro={
        codeSent
          ? "Every other session is logged out once the new password is set."
          : "Enter your email and we send you a 6-digit code to set a new password."
      }
      footer={
        <>
          Remembered it? <Link to="/app/login">Log in</Link>
        </>
      }
    >
      {codeSent ? (
        <>
          <p role="status">
            If the email belongs to an account, a code is on its way. Check your inbox.
          </p>
          <form className="form auth-form" noValidate onSubmit={onConfirm}>
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
              error={confirmErrors.code}
              onChange={(event) => setCode(event.target.value)}
            />
            <PasswordField
              label="New password"
              name="newPassword"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_CHARACTERS}
              maxLength={MAX_PASSWORD_CHARACTERS}
              hint={`${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`}
              value={newPassword}
              error={confirmErrors.newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            {confirmMutation.isError && (
              <p className="error" role="alert">
                {describeError(confirmMutation.error, { 400: CODE_NOT_VALID })}
              </p>
            )}
            <button type="submit" className="auth-submit" disabled={confirmMutation.isPending}>
              {confirmMutation.isPending && <span className="spinner" aria-hidden="true" />}
              {confirmMutation.isPending ? "Setting…" : "Set the password"}
            </button>
          </form>
          <div className="form auth-form">
            <button
              type="button"
              className="secondary"
              disabled={askMutation.isPending}
              onClick={onResend}
            >
              {askMutation.isPending ? "Sending…" : "Send a new code"}
            </button>
            {resent && (
              <p role="status">If the email belongs to an account, a new code is on its way.</p>
            )}
            {askMutation.isError && (
              <p className="error" role="alert">
                {describeError(askMutation.error)}
              </p>
            )}
          </div>
        </>
      ) : (
        <form className="form auth-form" noValidate onSubmit={onAsk}>
          <AuthField
            label="Email"
            icon={<MailIcon />}
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            error={askErrors.email}
            onChange={(event) => setEmail(event.target.value)}
          />
          {askMutation.isError && (
            <p className="error" role="alert">
              {describeError(askMutation.error)}
            </p>
          )}
          <button type="submit" className="auth-submit" disabled={askMutation.isPending}>
            {askMutation.isPending && <span className="spinner" aria-hidden="true" />}
            {askMutation.isPending ? "Sending…" : "Send the code"}
          </button>
        </form>
      )}
    </AuthFrame>
  );
}
