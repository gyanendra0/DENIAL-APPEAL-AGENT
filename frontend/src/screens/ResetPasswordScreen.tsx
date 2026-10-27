import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkResetConfirm,
  focusFirstError,
} from "../auth/credentialCheck";
import type { FieldErrors, ResetConfirmField } from "../auth/credentialCheck";
import {
  FORGOT_PASSWORD_PATH,
  PASSWORD_SET_STATE,
  RESET_PASSWORD_PATH,
  tokenFromHash,
} from "../auth/resetLink";
import { useConfirmPasswordReset } from "../auth/session";
import { PasswordField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { describeError } from "./errorMessage";

// The API's 400 is one sentence for every token problem: damaged, expired, used or replaced.
const LINK_NOT_VALID = "The reset link is not valid or has expired.";

// Sets a new password with the token from the emailed link. The token is read once and then taken
// out of the address bar, so it stays out of the history and off the screen; a reload after that
// needs the link again. The API does not log in, so a success goes to the login screen.
export function ResetPasswordScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => tokenFromHash(location.hash));
  const [newPassword, setNewPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors<ResetConfirmField>>({});
  const confirmMutation = useConfirmPasswordReset();

  useEffect(() => {
    if (location.hash !== "") {
      void navigate(RESET_PASSWORD_PATH, { replace: true });
    }
  }, [location.hash, navigate]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (token === null) {
      return;
    }
    const found = checkResetConfirm({ newPassword });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    confirmMutation.mutate(
      { token, new_password: newPassword },
      {
        onSuccess: () => {
          void navigate("/app/login", { replace: true, state: PASSWORD_SET_STATE });
        },
      },
    );
  }

  // No token in the link is told the same as a token the API refused.
  const linkNotValid =
    token === null ||
    (confirmMutation.error instanceof ApiError && confirmMutation.error.status === 400);

  return (
    <AuthFrame
      title="Set a new password"
      intro="Every other session is logged out once the new password is set."
      footer={
        <>
          Remembered it? <Link to="/app/login">Log in</Link>
        </>
      }
    >
      <form className="form auth-form" noValidate onSubmit={onSubmit}>
        <PasswordField
          label="New password"
          name="newPassword"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_CHARACTERS}
          maxLength={MAX_PASSWORD_CHARACTERS}
          hint={`${MIN_PASSWORD_CHARACTERS} to ${MAX_PASSWORD_CHARACTERS} characters.`}
          value={newPassword}
          error={errors.newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        {linkNotValid ? (
          <p className="error" role="alert">
            {LINK_NOT_VALID} <Link to={FORGOT_PASSWORD_PATH}>Ask for a new link</Link>
          </p>
        ) : (
          confirmMutation.isError && (
            <p className="error" role="alert">
              {describeError(confirmMutation.error)}
            </p>
          )
        )}
        <button
          type="submit"
          className="auth-submit"
          disabled={token === null || confirmMutation.isPending}
        >
          {confirmMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {confirmMutation.isPending ? "Setting…" : "Set the password"}
        </button>
      </form>
    </AuthFrame>
  );
}
