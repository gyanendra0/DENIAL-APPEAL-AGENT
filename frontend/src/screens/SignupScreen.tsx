import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { HOME_PATH } from "../auth/redirect";
import { useSignup } from "../auth/session";
import { describeError } from "./errorMessage";

// The same limits as `src/api/passwords.py`; the API checks them again.
export const MIN_PASSWORD_CHARACTERS = 12;
export const MAX_PASSWORD_CHARACTERS = 128;

// A 409 says which name is taken ("the email is taken"); that text comes from the API, not the user.
function signupError(error: unknown): string {
  if (error instanceof ApiError && error.kind === "conflict") {
    return `${error.message.charAt(0).toUpperCase()}${error.message.slice(1)}.`;
  }
  return describeError(error);
}

export function SignupScreen() {
  const [accountName, setAccountName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const signupMutation = useSignup();
  const navigate = useNavigate();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    signupMutation.mutate(
      { account_name: accountName, email, password },
      { onSuccess: () => void navigate(HOME_PATH, { replace: true }) },
    );
  }

  return (
    <main className="page narrow">
      <h1>Sign up</h1>
      <p className="muted">Creates a new account; you become its admin.</p>
      <form className="form" onSubmit={onSubmit}>
        <label>
          Account name
          <input
            type="text"
            autoComplete="organization"
            required
            maxLength={200}
            value={accountName}
            onChange={(event) => setAccountName(event.target.value)}
          />
        </label>
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label>
          Password ({MIN_PASSWORD_CHARACTERS} to {MAX_PASSWORD_CHARACTERS} characters)
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_CHARACTERS}
            maxLength={MAX_PASSWORD_CHARACTERS}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {signupMutation.isError && (
          <p className="error" role="alert">
            {signupError(signupMutation.error)}
          </p>
        )}
        <button type="submit" disabled={signupMutation.isPending}>
          {signupMutation.isPending ? "Signing up…" : "Sign up"}
        </button>
      </form>
      <p>
        Have an account? <Link to="/app/login">Log in</Link>
      </p>
    </main>
  );
}
