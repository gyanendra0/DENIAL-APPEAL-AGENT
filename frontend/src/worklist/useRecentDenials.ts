// The newest few jobs for the sidebar's "Recent" list. Its key starts with "worklist", so whatever
// refreshes the worklist (an approval, a rejection) refreshes this list too.

import { useQuery } from "@tanstack/react-query";

import { fetchWorklist } from "../api/worklist";

export const RECENT_DENIALS_LIMIT = 10;

export function recentDenialsKey() {
  return ["worklist", "recent"] as const;
}

export function useRecentDenials() {
  return useQuery({
    queryKey: recentDenialsKey(),
    queryFn: () =>
      fetchWorklist({
        status: null,
        reviewStatus: null,
        before: null,
        limit: RECENT_DENIALS_LIMIT,
      }),
    retry: false,
  });
}
