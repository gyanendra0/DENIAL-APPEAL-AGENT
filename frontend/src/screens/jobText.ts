// Plain sentences for where a job is and how it ended. Shared by the upload and worklist screens.

import type {
  DenialReasonCategory,
  DraftReviewStatus,
  EndState,
  JobFailure,
  JobResponse,
  JobStatus,
} from "../api/types";

export const STATUS_TEXT: Readonly<Record<JobStatus, string>> = {
  queued: "Waiting to start",
  running: "Reading the letter and drafting",
  done: "Finished",
  failed: "Failed",
};

export const END_STATE_TEXT: Readonly<Record<EndState, string>> = {
  needs_review: "Draft ready for review",
  needs_review_no_draft: "A person must review it; no draft was written",
  stopped_by_rules: "Stopped by the rules (deadline or amount); no draft was written",
  below_threshold: "The appeal is unlikely to win; no draft was written",
  draft_failed: "No draft passed the checks; a person must review it",
};

export const FAILURE_TEXT: Readonly<Record<JobFailure, string>> = {
  extraction_failed: "The letter could not be read into fields",
  budget_exceeded: "The monthly model budget is used up",
  llm_unavailable: "The model service could not be reached",
  evidence_not_ready: "The policy evidence is not ready",
  interrupted: "The server restarted while the job ran",
  internal_error: "Something went wrong on the server",
};

export const REVIEW_STATUS_TEXT: Readonly<Record<DraftReviewStatus, string>> = {
  pending: "Waiting for review",
  approved: "Approved",
  rejected: "Rejected",
};

export const DENIAL_REASON_TEXT: Readonly<Record<DenialReasonCategory, string>> = {
  noncovered: "Not covered",
  medical_necessity: "Medical necessity",
  duplicate: "Duplicate claim",
  benefits_exhausted: "Benefits used up",
  coordination_of_benefits: "Coordination of benefits",
  other: "Other",
};

// One line for a job: its end state when done, its failure when failed, else its status. A
// worklist item has these three fields too.
export function describeJob(job: Pick<JobResponse, "status" | "failure" | "end_state">): string {
  if (job.status === "done" && job.end_state !== null) {
    return END_STATE_TEXT[job.end_state];
  }
  if (job.status === "failed" && job.failure !== null) {
    return `${STATUS_TEXT.failed}: ${FAILURE_TEXT[job.failure].toLowerCase()}`;
  }
  return STATUS_TEXT[job.status];
}
