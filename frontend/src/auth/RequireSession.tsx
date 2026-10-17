import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { describeError } from "../screens/errorMessage";
import { useSession } from "./session";

export interface FromState {
  from: string;
}

// Renders `children` only for a logged-in user. Without a session it sends the user to the login
// screen and remembers the page they asked for, so login can bring them back to it.
export function RequireSession({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();

  if (session.isPending) {
    return <p className="page">Loading…</p>;
  }
  if (session.isError) {
    return (
      <p className="page error" role="alert">
        {describeError(session.error)}
      </p>
    );
  }
  if (session.data === null) {
    const state: FromState = { from: location.pathname + location.search };
    return <Navigate to="/app/login" replace state={state} />;
  }
  return children;
}
