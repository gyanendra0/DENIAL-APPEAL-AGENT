import { Outlet, useNavigate } from "react-router";

import { useLogout, useSession } from "../auth/session";
import { describeError } from "./errorMessage";

// The frame of every logged-in screen. It sits inside the guard, so the session is there.
export function AppLayout() {
  const session = useSession();
  const logoutMutation = useLogout();
  const navigate = useNavigate();
  const user = session.data;

  function onLogout() {
    logoutMutation.mutate(undefined, {
      onSuccess: () => void navigate("/app/login", { replace: true }),
    });
  }

  return (
    <>
      <header className="app-header">
        <strong>Denial appeal agent</strong>
        {user && (
          <span className="muted">
            {user.account_name} · {user.email} ({user.role})
          </span>
        )}
        <button type="button" onClick={onLogout} disabled={logoutMutation.isPending}>
          Log out
        </button>
      </header>
      {logoutMutation.isError && (
        <p className="page error" role="alert">
          {describeError(logoutMutation.error)}
        </p>
      )}
      <main className="page">
        <Outlet />
      </main>
    </>
  );
}
