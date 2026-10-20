// Saving an edited draft, approving and rejecting it. After each, and after a 409 that says the
// draft is already final, the detail, the worklist and the overview are read again.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import { approveDraft, rejectDraft, saveDraft } from "../api/review";
import type { AppealLetter, DraftRejectReason } from "../api/types";
import { jobDetailKey } from "../jobs/useJobDetail";
import { overviewKey } from "../overview/useOverview";

// The API's 409 sentence for an edit or approval made on an older version
// (`STALE_VERSION` in `src/api/routes/review.py`). Any other 409 means the draft is final.
export const STALE_VERSION = "the draft has a newer version; reload it";

export function isStaleVersion(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "conflict" && error.message === STALE_VERSION;
}

export function isAlreadyDecided(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "conflict" && !isStaleVersion(error);
}

// Marks every screen an action changes as stale; the shown ones are read again at once.
export function refreshAfterReview(queryClient: QueryClient, jobId: number): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: jobDetailKey(jobId) }),
    queryClient.invalidateQueries({ queryKey: ["worklist"] }),
    queryClient.invalidateQueries({ queryKey: overviewKey() }),
  ]).then(() => undefined);
}

// Shared by the three actions: refresh on success (the action stays pending until the detail
// is read again, so no button shows a state that has passed) and on a draft already final.
function useReviewMutation<TInput, TResult>(
  jobId: number,
  send: (input: TInput) => Promise<TResult>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: () => refreshAfterReview(queryClient, jobId),
    onError: (error: Error) => {
      if (isAlreadyDecided(error)) {
        void refreshAfterReview(queryClient, jobId);
      }
    },
  });
}

export function useSaveDraft(jobId: number) {
  return useReviewMutation(jobId, (input: { letter: AppealLetter; basedOnVersion: number }) =>
    saveDraft(jobId, input.letter, input.basedOnVersion),
  );
}

export function useApproveDraft(jobId: number) {
  return useReviewMutation(jobId, (version: number) => approveDraft(jobId, version));
}

export function useRejectDraft(jobId: number) {
  return useReviewMutation(jobId, (reason: DraftRejectReason) => rejectDraft(jobId, reason));
}
