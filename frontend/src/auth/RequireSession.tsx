import { useState } from "react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { describeError } from "../screens/errorMessage";
import { useSession } from "./session";

// `userId` is the user whose session ended (after a 401); absent when no user was logged in.
export interface FromState {
  from: string;
  userId?: number;
}

// Renders `children` only for a logged-in user. Without a session it sends the user to the login
// screen and remembers the page they asked for, so login can bring them back to it, but only when
// the same user logs in again.
export function RequireSession({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
  // The last user seen, kept after the session turns null. Set during render when the user
  // changes (React's pattern for information from earlier renders), so no effect or ref is needed.
  const [lastUserId, setLastUserId] = useState<number | null>(null);
  const userId = session.data?.user_id ?? null;
  if (userId !== null && userId !== lastUserId) {
    setLastUserId(userId);
  }

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
    const from = location.pathname + location.search;
    const state: FromState = lastUserId === null ? { from } : { from, userId: lastUserId };
    return <Navigate to="/app/login" replace state={state} />;
  }
  return children;
}
