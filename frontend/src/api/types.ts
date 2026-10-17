// The API answers the screens read, written by hand. Each type mirrors the Python model named
// above it; the string unions are copied from the enums in `src/db/models.py`. Keep them in step.
// Times are ISO 8601 strings, as FastAPI sends them.

// UserRole
export type UserRole = "viewer" | "reviewer" | "admin";

// JobStatus
export type JobStatus = "queued" | "running" | "done" | "failed";

// JobFailure
export type JobFailure =
  | "extraction_failed"
  | "budget_exceeded"
  | "llm_unavailable"
  | "evidence_not_ready"
  | "interrupted"
  | "internal_error";

// EndState
export type EndState =
  | "needs_review"
  | "needs_review_no_draft"
  | "stopped_by_rules"
  | "below_threshold"
  | "draft_failed";

// DraftReviewStatus
export type DraftReviewStatus = "pending" | "approved" | "rejected";

// DenialReasonCategory
export type DenialReasonCategory =
  | "noncovered"
  | "medical_necessity"
  | "duplicate"
  | "benefits_exhausted"
  | "coordination_of_benefits"
  | "other";

// src/api/schemas.py UserResponse
export interface UserResponse {
  user_id: number;
  email: string;
  role: UserRole;
  account_id: number;
  account_name: string;
}

// src/api/routes/jobs.py UploadAccepted
export interface UploadAccepted {
  job_id: number;
  status: JobStatus;
}

// src/api/routes/jobs.py JobResponse: `failure` is set only when failed, `end_state` only when done
export interface JobResponse {
  job_id: number;
  status: JobStatus;
  failure: JobFailure | null;
  end_state: EndState | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// src/review/worklist.py WorklistItem: `review_status` is null when the job has no draft
export interface WorklistItem {
  job_id: number;
  file_name: string;
  uploaded_at: string;
  uploaded_by: string;
  status: JobStatus;
  failure: JobFailure | null;
  end_state: EndState | null;
  review_status: DraftReviewStatus | null;
  denial_reason: DenialReasonCategory | null;
  win_probability: number | null;
}

// src/review/worklist.py WorklistPage: `next_before` is null on the last page
export interface WorklistPage {
  items: WorklistItem[];
  next_before: number | null;
}
