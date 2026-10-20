// The denial detail of one job and the review of its draft: edit, approve, reject. The API
// decides who may act; a refused letter comes back as an ApiError with `letterProblems`.

import { apiRequest, jsonRequest } from "./client";
import type {
  AppealLetter,
  DraftRejectReason,
  DraftVersionResponse,
  JobDetail,
  ReviewResponse,
} from "./types";

export function fetchJobDetail(jobId: number): Promise<JobDetail> {
  return apiRequest<JobDetail>(`/jobs/${jobId}/detail`);
}

// Sends the whole edited letter; `basedOnVersion` is the `latest_version` the edit started from.
export function saveDraft(
  jobId: number,
  letter: AppealLetter,
  basedOnVersion: number,
): Promise<DraftVersionResponse> {
  return apiRequest<DraftVersionResponse>(
    `/jobs/${jobId}/draft`,
    jsonRequest("PUT", { letter, based_on_version: basedOnVersion }),
  );
}

// Approves `version`, which must be the latest. Nothing is sent to an insurer.
export function approveDraft(jobId: number, version: number): Promise<ReviewResponse> {
  return apiRequest<ReviewResponse>(
    `/jobs/${jobId}/draft/approve`,
    jsonRequest("POST", { version }),
  );
}

export function rejectDraft(jobId: number, reason: DraftRejectReason): Promise<ReviewResponse> {
  return apiRequest<ReviewResponse>(`/jobs/${jobId}/draft/reject`, jsonRequest("POST", { reason }));
}
