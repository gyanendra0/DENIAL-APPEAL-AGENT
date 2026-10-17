// The worklist pages as one infinite query: each page asks for the jobs older than the last one.

import { useInfiniteQuery } from "@tanstack/react-query";

import { fetchWorklist } from "../api/worklist";
import type { WorklistFilters } from "../api/worklist";

// The API's default page size (`DEFAULT_PAGE_SIZE` in `src/review/paging.py`).
export const WORKLIST_PAGE_SIZE = 50;

export function worklistKey(filters: WorklistFilters) {
  return ["worklist", filters.status, filters.reviewStatus] as const;
}

export function useWorklist(filters: WorklistFilters) {
  return useInfiniteQuery({
    queryKey: worklistKey(filters),
    queryFn: ({ pageParam }) =>
      fetchWorklist({ ...filters, before: pageParam, limit: WORKLIST_PAGE_SIZE }),
    initialPageParam: null as number | null,
    // `next_before` is null on the last page; undefined tells TanStack Query there is no next one.
    getNextPageParam: (lastPage) => lastPage.next_before ?? undefined,
    retry: false,
  });
}
