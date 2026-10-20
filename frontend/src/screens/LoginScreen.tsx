import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { afterLoginPath } from "../auth/redirect";
import { clearAccountData, useLogin } from "../auth/session";
import { describeError } from "./errorMessage";

export function LoginScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const loginMutation = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  // A 401 from any call ends up here; by now the screens that showed the account's data are gone,
  // so emptying the cache makes none of them ask again.
  useEffect(() => clearAccountData(queryClient), [queryClient]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    loginMutation.mutate(
      { email, password },
      {
        onSuccess: (user) => {
          void navigate(afterLoginPath(location.state, user.user_id), { replace: true });
        },
      },
    );
  }

  return (
    <main className="page narrow">
      <h1>Log in</h1>
      <form className="form" onSubmit={onSubmit}>
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
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {loginMutation.isError && (
          <p className="error" role="alert">
            {describeError(loginMutation.error, { 401: "Email or password is wrong." })}
          </p>
        )}
        <button type="submit" disabled={loginMutation.isPending}>
          {loginMutation.isPending ? "Logging in…" : "Log in"}
        </button>
      </form>
      <p>
        No account yet? <Link to="/app/signup">Sign up</Link>
      </p>
    </main>
  );
}
