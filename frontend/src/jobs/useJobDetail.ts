// The denial detail of one job: its letter text, extracted fields, trace and draft.

import { useQuery } from "@tanstack/react-query";

import { fetchJobDetail } from "../api/review";

export function jobDetailKey(jobId: number) {
  return ["job-detail", jobId] as const;
}

// Read once the job is finished (`enabled`); a 404 stays a 404, so no retry.
export function useJobDetail(jobId: number, enabled: boolean) {
  return useQuery({
    queryKey: jobDetailKey(jobId),
    queryFn: () => fetchJobDetail(jobId),
    enabled,
    retry: false,
  });
}
