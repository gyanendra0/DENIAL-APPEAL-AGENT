import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { checkLogin, focusFirstError } from "../auth/credentialCheck";
import type { FieldErrors, LoginField } from "../auth/credentialCheck";
import { afterLoginPath } from "../auth/redirect";
import { clearAccountData, useLogin } from "../auth/session";
import { AuthField, PasswordField } from "./AuthField";
import { AuthFrame } from "./AuthFrame";
import { describeError } from "./errorMessage";
import { MailIcon } from "./icons";

export function LoginScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors<LoginField>>({});
  const loginMutation = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  // A 401 from any call ends up here; by now the screens that showed the account's data are gone,
  // so emptying the cache makes none of them ask again.
  useEffect(() => clearAccountData(queryClient), [queryClient]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = checkLogin({ email, password });
    setErrors(found);
    if (focusFirstError(event.currentTarget, found)) {
      return;
    }
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
    <AuthFrame
      title="Log in"
      intro="Review insurance denials and approve cited appeal letters."
      footer={
        <>
          No account yet? <Link to="/app/signup">Sign up</Link>
        </>
      }
    >
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
          autoComplete="current-password"
          required
          value={password}
          error={errors.password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {loginMutation.isError && (
          <p className="error" role="alert">
            {describeError(loginMutation.error, {
              401: "Email or password is wrong.",
              429: "Too many failed logins. Try again in a few minutes.",
            })}
          </p>
        )}
        <button type="submit" className="auth-submit" disabled={loginMutation.isPending}>
          {loginMutation.isPending && <span className="spinner" aria-hidden="true" />}
          {loginMutation.isPending ? "Logging in…" : "Log in"}
        </button>
      </form>
    </AuthFrame>
  );
}
