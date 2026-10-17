// The upload mutation and the job query, which asks again while the job is not finished.

import { useMutation, useQuery } from "@tanstack/react-query";

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
  return useMutation({ mutationFn: (file: File) => uploadLetter(file) });
}

// Polls every JOB_POLL_MS until the job is finished, and stops on an error (a 404 stays a 404).
export function useJob(jobId: number) {
  return useQuery({
    queryKey: jobKey(jobId),
    queryFn: () => fetchJob(jobId),
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
