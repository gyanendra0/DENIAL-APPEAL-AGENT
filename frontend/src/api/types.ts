// The API answers the screens read, written by hand. Each type mirrors the Python model named
// above it; the string unions are copied from the enums in `src/db/models.py` (or the file named
// above them). Keep them in step. Times and dates are ISO 8601 strings, as FastAPI sends them;
// a Python `Decimal` arrives as a string (`"0.001141"`) and stays one.

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

// UploadFileType
export type UploadFileType = "text" | "pdf";

// DraftRejectReason
export type DraftRejectReason =
  "wrong_fact" | "no_policy_argument" | "citation_does_not_fit" | "poor_wording" | "other";

// EvidenceSource
export type EvidenceSource = "cms_ncd" | "cms_manual";

// LlmProvider
export type LlmProvider = "primary" | "fallback";

// src/agent/trace.py StepStatus
export type StepStatus = "done" | "stopped" | "failed" | "not_run";

// src/agent/trace.py DraftFailure
export type DraftFailure =
  "blocked" | "cut_off" | "not_parseable" | "placeholder_text" | "fact_check";

// src/rules/verdict.py RuleOutcome
export type RuleOutcome = "pass" | "must_review" | "hard_fail";

// src/rules/verdict.py RuleReason
export type RuleReason =
  | "deadline_not_after_letter_date"
  | "deadline_missing"
  | "deadline_passed"
  | "amount_missing"
  | "amount_zero"
  | "amount_below_floor";

// src/drafting/citation_validator.py CitationProblemKind
export type CitationProblemKind =
  "policy_without_citation" | "unknown_label" | "quote_too_short" | "quote_not_in_evidence";

// src/drafting/letter_check.py LetterProblemKind
export type LetterProblemKind =
  | "policy_without_citation"
  | "chunk_not_found"
  | "quote_too_short"
  | "quote_not_in_evidence"
  | "placeholder_text"
  | "amount_not_a_denied_line"
  | "other_as_reason"
  | "edit_not_allowed";

// src/drafting/schemas.py ParagraphKind
export type ParagraphKind = "policy" | "other";

// src/api/schemas.py UserResponse
export interface UserResponse {
  user_id: number;
  email: string;
  role: UserRole;
  account_id: number;
  account_name: string;
}

// src/api/schemas.py AuthOptions: the ways in besides email and password.
export interface AuthOptions {
  google: boolean;
}

// src/api/schemas.py GoogleSignupInfo: the verified Google email a pending sign-up will use, and
// the shared account it will join.
export interface GoogleSignupInfo {
  email: string;
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

// src/drafting/schemas.py Citation: the four fields that name a chunk, and the exact quote
export interface Citation {
  source: EvidenceSource;
  source_document_id: string;
  chunk_index: number;
  text_sha256: string;
  quote: string;
}

// src/drafting/schemas.py LetterParagraph: only a `policy` paragraph has citations
export interface LetterParagraph {
  kind: ParagraphKind;
  text: string;
  citations: Citation[];
}

// src/drafting/schemas.py AppealLetter
export interface AppealLetter {
  paragraphs: LetterParagraph[];
}

// src/extraction/schemas.py Extracted: `value` is null when the letter does not give it
export interface Extracted<T> {
  value: T | null;
  confidence: number;
}

// src/extraction/schemas.py ExtractedDeniedLine: amounts are decimals (strings)
export interface ExtractedDeniedLine {
  line_number: number;
  hcpcs_code: string | null;
  allowed_charge_amount: string;
  payment_amount: string;
  reason_category: DenialReasonCategory;
}

// src/extraction/schemas.py DenialLetterExtraction: dates are "YYYY-MM-DD", amounts strings
export interface DenialLetterExtraction {
  claim_number: Extracted<string>;
  service_from_date: Extracted<string>;
  service_thru_date: Extracted<string>;
  diagnosis_codes: Extracted<string[]>;
  denied_lines: Extracted<ExtractedDeniedLine[]>;
  total_allowed_charge_amount: Extracted<string>;
  total_payment_amount: Extracted<string>;
  denial_reason_category: Extracted<DenialReasonCategory>;
  patient_name: Extracted<string>;
  member_id: Extracted<string>;
  provider_name: Extracted<string>;
  payer_name: Extracted<string>;
  reference_number: Extracted<string>;
  letter_date: Extracted<string>;
  appeal_deadline: Extracted<string>;
}

// src/agent/trace.py ExtractionStep
export interface ExtractionStep {
  status: StepStatus;
  prompt_version: string;
  text_sha256: string;
}

// src/agent/trace.py RulesStep: `outcome` is null when the step failed
export interface RulesStep {
  status: StepStatus;
  rules_version: string;
  today: string;
  amount_floor: string;
  outcome: RuleOutcome | null;
  reasons: RuleReason[];
}

// src/agent/trace.py ScoringStep: all null when not run
export interface ScoringStep {
  status: StepStatus;
  feature_version: string | null;
  model_version: string | null;
  score: number | null;
  threshold: number | null;
}

// src/agent/trace.py EvidenceReference: no chunk text, only what names the chunk
export interface EvidenceReference {
  source: EvidenceSource;
  source_document_id: string;
  chunk_index: number;
  text_sha256: string;
  similarity: number;
}

// src/agent/trace.py RetrievalStep: matches closest first
export interface RetrievalStep {
  status: StepStatus;
  search_question_version: string | null;
  embedding_model: string | null;
  min_similarity: number | null;
  matches: EvidenceReference[];
}

// src/agent/trace.py DraftingStep
export interface DraftingStep {
  status: StepStatus;
  prompt_version: string | null;
  provider: LlmProvider | null;
  model_name: string | null;
  cited_paragraphs: number | null;
  failure: DraftFailure | null;
  problem_kinds: CitationProblemKind[];
  attempts: number | null;
}

// src/agent/trace.py AgentTrace
export interface AgentTrace {
  end_state: EndState;
  extraction: ExtractionStep;
  rules: RulesStep;
  scoring: ScoringStep;
  retrieval: RetrievalStep;
  drafting: DraftingStep;
  agent_version: string;
}

// src/review/detail.py DraftDetail: `latest` is `original` until someone edits it
export interface DraftDetail {
  prompt_version: string;
  model_name: string;
  original: AppealLetter;
  latest_version: number;
  latest: AppealLetter;
  review_status: DraftReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reject_reason: DraftRejectReason | null;
  approved_version: number | null;
}

// src/review/detail.py JobDetail: a part the job did not reach is null
export interface JobDetail {
  job_id: number;
  file_name: string;
  file_type: UploadFileType;
  uploaded_at: string;
  uploaded_by: string;
  status: JobStatus;
  failure: JobFailure | null;
  end_state: EndState | null;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: string;
  text: string;
  extraction: DenialLetterExtraction | null;
  trace: AgentTrace | null;
  draft: DraftDetail | null;
}

// src/api/routes/review.py DraftVersionResponse
export interface DraftVersionResponse {
  job_id: number;
  version: number;
}

// src/api/routes/review.py ReviewResponse
export interface ReviewResponse {
  job_id: number;
  review_status: DraftReviewStatus;
}

// src/drafting/letter_check.py LetterProblem: indexes count from 0; `citation_index` is null for
// a problem of the whole paragraph
export interface LetterProblem {
  kind: LetterProblemKind;
  paragraph_index: number;
  citation_index: number | null;
}

// src/review/overview.py Overview: every enum value is present (0 when none); costs are decimals,
// null for anyone but an admin (only admins see what the model costs)
export interface Overview {
  jobs_by_status: Record<JobStatus, number>;
  jobs_by_end_state: Record<EndState, number>;
  drafts_by_review_status: Record<DraftReviewStatus, number>;
  cost_usd_this_month: string | null;
  cost_usd_total: string | null;
  average_seconds: number | null;
}
