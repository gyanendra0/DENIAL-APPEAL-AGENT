import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api/client";
import { SESSION_KEY, clearAccountData } from "./auth/session";

// A 401 from any call means the session has ended: the session is set to null, so the guard
// sends the user to the login screen, and the rest of the cache is emptied, so the next user to
// log in never sees this one's data. Wrong credentials at login are a 401 too; the session is
// already null and the cache already empty there, so nothing changes.
export function createQueryClient(): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => endSessionOn401(error) }),
    mutationCache: new MutationCache({ onError: (error) => endSessionOn401(error) }),
  });
  function endSessionOn401(error: unknown) {
    if (error instanceof ApiError && error.kind === "unauthorized") {
      queryClient.setQueryData(SESSION_KEY, null);
      clearAccountData(queryClient);
    }
  }
  return queryClient;
}
