import { useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router";

import { ApiError } from "../api/client";
import {
  MAX_PASSWORD_CHARACTERS,
  MIN_PASSWORD_CHARACTERS,
  checkPasswordChange,
  focusFirstError,
} from "../auth/credentialCheck";
import type { FieldErrors, PasswordChangeField } from "../auth/credentialCheck";
import { useChangePassword, useDeleteAccount, useSession } from "../auth/session";
import { useNotify } from "../notify/notify";
import { ACCOUNT_OPEN_PARAM, readAccountForm } from "./accountPaths";
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

// The account menu links here with `?open=password` or `?open=delete`, which opens that form
// straight away and leaves the other out; the key starts the form afresh when the menu asks for
// another one.
export function AccountScreen() {
  const session = useSession();
  const [searchParams] = useSearchParams();
  const form = readAccountForm(searchParams.get(ACCOUNT_OPEN_PARAM));
  // The form the menu asked for is shown alone; with none asked for, both are. A shared account
  // has no delete, so it always keeps the password part (never an empty card).
  const showsDelete = session.data?.account_kind === "personal" && form !== "password";
  const showsPassword = !(showsDelete && form === "delete");

  return (
    <div className="centered-screen">
      <div className="screen-card">
        <h1>Account</h1>
        {session.data && <p className="muted card-lead">{session.data.email}</p>}
        {showsPassword && (
          <PasswordChange key={`password-${form}`} startOpen={form === "password"} />
        )}
        {showsDelete && <DeleteAccount key={`delete-${form}`} startOpen={form === "delete"} />}
      </div>
    </div>
  );
}

// Every role can change their own password. The form stays closed until the user asks for it;
// Cancel closes it again and forgets what was typed. The API keeps this browser logged in with a
// new cookie and ends every other session.
function PasswordChange({ startOpen }: { startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors<PasswordChangeField>>({});
  const changeMutation = useChangePassword();
  const notify = useNotify();

  function onCancel() {
    setOpen(false);
    setCurrentPassword("");
    setNewPassword("");
    setErrors({});
    changeMutation.reset();
  }

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
          setOpen(false);
          notify({
            tone: "success",
            title: "Password changed",
            detail: "Other sessions are logged out.",
          });
        },
      },
    );
  }

  return (
    <section className="card-section" aria-label="Password">
      <h2>Password</h2>
      {!open ? (
        <button
          type="button"
          className="secondary"
          onClick={() => {
            changeMutation.reset();
            setOpen(true);
          }}
        >
          Change password
        </button>
      ) : (
        <form className="form" noValidate onSubmit={onSubmit}>
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
          <button type="submit" className="auth-submit" disabled={changeMutation.isPending}>
            {changeMutation.isPending && <span className="spinner" aria-hidden="true" />}
            {changeMutation.isPending ? "Changing…" : "Change password"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={changeMutation.isPending}
            onClick={onCancel}
          >
            Cancel
          </button>
        </form>
      )}
    </section>
  );
}

// Only an individual's personal account can be deleted; the first click opens the confirm step.
// A user who logs in only with Google has no password and leaves the field empty; the API then
// wants a login within the last 10 minutes. When the account is gone the session is null and the
// guard sends the user to the login screen.
function DeleteAccount({ startOpen }: { startOpen: boolean }) {
  const [confirming, setConfirming] = useState(startOpen);
  const [password, setPassword] = useState("");
  const deleteMutation = useDeleteAccount();
  const notify = useNotify();

  function onCancel() {
    setConfirming(false);
    setPassword("");
    deleteMutation.reset();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    deleteMutation.mutate(
      { password: password === "" ? null : password },
      {
        onSuccess: () =>
          notify({
            tone: "info",
            title: "Account deleted",
            detail: "Your letters and drafts are gone.",
          }),
      },
    );
  }

  return (
    <section className="card-section" aria-label="Delete my account">
      <h2>Delete account</h2>
      {!confirming ? (
        <button type="button" className="secondary" onClick={() => setConfirming(true)}>
          Delete my account
        </button>
      ) : (
        <form className="form" noValidate onSubmit={onSubmit}>
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
