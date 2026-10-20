import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api/client";
import { SESSION_KEY } from "./auth/session";

// A 401 from any call means the session has ended: the session is set to null, so the guard
// sends the user to the login screen, and that screen empties the rest of the cache when it opens,
// so the next user to log in never sees this one's data. Emptying it here instead would remove the
// failed query while its screen is still shown, and that screen would ask for it once more before
// the guard sends the user away. Wrong credentials at login are a 401 too; the session is already
// null there, so nothing changes.
export function createQueryClient(): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => endSessionOn401(error) }),
    mutationCache: new MutationCache({ onError: (error) => endSessionOn401(error) }),
  });
  function endSessionOn401(error: unknown) {
    if (error instanceof ApiError && error.kind === "unauthorized") {
      queryClient.setQueryData(SESSION_KEY, null);
    }
  }
  return queryClient;
}
