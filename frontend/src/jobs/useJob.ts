// The upload mutation and the job query, which asks again while the job is not finished. A new
// upload and a job that finishes both refresh the worklist (and so the sidebar's "Recent" list).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchJob, uploadLetter } from "../api/jobs";
import type { JobResponse } from "../api/types";

export const JOB_POLL_MS = 2000;

export function jobKey(jobId: number) {
  return ["job", jobId] as const;
}

// A job is finished once it is `done` or `failed`; its status does not change after that.
export function isJobFinished(job: JobResponse): boolean {
  return job.status === "done" || job.status === "failed";
}

export function useUploadLetter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadLetter(file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["worklist"] }),
  });
}

// Polls every JOB_POLL_MS until the job is finished, and stops on an error (a 404 stays a 404).
// The first answer that shows the job finished refreshes the worklist once.
export function useJob(jobId: number) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: jobKey(jobId),
    queryFn: async () => {
      const before = queryClient.getQueryData<JobResponse>(jobKey(jobId));
      const job = await fetchJob(jobId);
      if (isJobFinished(job) && (before === undefined || !isJobFinished(before))) {
        void queryClient.invalidateQueries({ queryKey: ["worklist"] });
      }
      return job;
    },
    retry: false,
    refetchInterval: (query) => {
      const job = query.state.data;
      if (query.state.status === "error" || (job !== undefined && isJobFinished(job))) {
        return false;
      }
      return JOB_POLL_MS;
    },
  });
}
