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
import { useChangePassword, useDeleteAccount, useSession } from "../auth/session";
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

// The API's 400 (wrong password), 403 (a login too old for a Google-only user) and 409 (a letter
// still being read) are fixed sentences that say what to do; the screen shows them. A 429 is the
// login limit and keeps the usual line.
function deleteError(error: unknown): string {
  if (error instanceof ApiError && [400, 403, 409].includes(error.status ?? 0)) {
    return asSentence(error.message);
  }
  return describeError(error);
}

// Every role can change their own password. The API keeps this browser logged in with a new
// cookie and ends every other session.
export function AccountScreen() {
  const session = useSession();

  return (
    <>
      <h1>Account</h1>
      <PasswordChange />
      {session.data?.account_kind === "personal" && <DeleteAccount />}
    </>
  );
}

function PasswordChange() {
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

// Only an individual's personal account can be deleted; the first click opens the confirm step.
// A user who logs in only with Google has no password and leaves the field empty; the API then
// wants a login within the last 10 minutes. When the account is gone the session is null and the
// guard sends the user to the login screen.
function DeleteAccount() {
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const deleteMutation = useDeleteAccount();

  function onCancel() {
    setConfirming(false);
    setPassword("");
    deleteMutation.reset();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    deleteMutation.mutate({ password: password === "" ? null : password });
  }

  return (
    <section aria-label="Delete my account">
      <h2>Delete my account</h2>
      {!confirming ? (
        <button type="button" className="secondary" onClick={() => setConfirming(true)}>
          Delete my account
        </button>
      ) : (
        <form className="form account-form" noValidate onSubmit={onSubmit}>
          <p className="notice">
            This deletes your account, every letter you uploaded and every draft. It cannot be
            undone.
          </p>
          <PasswordField
            name="deletePassword"
            autoComplete="current-password"
            hint="Leave empty if you sign in only with Google."
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {deleteMutation.isError && (
            <p className="error" role="alert">
              {deleteError(deleteMutation.error)}
            </p>
          )}
          <button type="submit" className="auth-submit" disabled={deleteMutation.isPending}>
            {deleteMutation.isPending && <span className="spinner" aria-hidden="true" />}
            {deleteMutation.isPending ? "Deleting…" : "Delete for good"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={deleteMutation.isPending}
            onClick={onCancel}
          >
            Cancel
          </button>
        </form>
      )}
    </section>
  );
}
