import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkPasswordChange,
  focusFirstError,
} from "../auth/credentialCheck";
import type { FieldErrors, PasswordChangeField } from "../auth/credentialCheck";
import { useChangePassword } from "../auth/session";
import { PasswordField } from "./AuthField";
import { asSentence, describeError } from "./errorMessage";

// The API's 400 is one of two fixed sentences: the current password is wrong, or the login has no
// password yet (a Google sign-up). The client has no kind for 400, so the screen shows the API's.
function passwordChangeError(error: unknown): string {
  if (error instanceof ApiError && error.status === 400) {
    return asSentence(error.message);
  }
  return describeError(error);
}

// Every role can change their own password. The API keeps this browser logged in with a new
// cookie and ends every other session.
export function AccountScreen() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors<PasswordChangeField>>({});
  const changeMutation = useChangePassword();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkPasswordChange({ currentPassword, newPassword });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
    changeMutation.mutate(
      { current_password: currentPassword, new_password: newPassword },
      {
        onSuccess: () => {
          setCurrentPassword("");
          setNewPassword("");
        },
      },
    );
  }

  return (
    <>
      <h1>Account</h1>
      <h2>Change password</h2>
      <form className="form account-form" noValidate onSubmit={onSubmit}>
        <PasswordField
          label="Current password"
          name="currentPassword"
          autoComplete="current-password"
          required
          value={currentPassword}
          error={errors.currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
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
          error={errors.newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        {changeMutation.isError && (
          <p className="error" role="alert">
            {passwordChangeError(changeMutation.error)}
          </p>
        )}
        {changeMutation.isSuccess && (
          <p role="status">Your password is changed. Other sessions are logged out.</p>
        )}
        <button type="submit" className="auth-submit" disabled={changeMutation.isPending}>
          {changeMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {changeMutation.isPending ? "Changing…" : "Change password"}
        </button>
      </form>
    </>
  );
}
