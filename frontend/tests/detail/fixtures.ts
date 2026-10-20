// A made-up denial detail for the detail tests. No real patient, claim or payer.

import type {
  AgentTrace,
  AppealLetter,
  DenialLetterExtraction,
  DraftDetail,
  JobDetail,
  LetterParagraph,
} from "../../src/api/types";

export const EXTRACTION: DenialLetterExtraction = {
  claim_number: { value: "MADE-UP-1001", confidence: 0.97 },
  service_from_date: { value: "2026-09-01", confidence: 0.95 },
  service_thru_date: { value: "2026-09-01", confidence: 0.95 },
  diagnosis_codes: { value: ["Z00.00", "R51.9"], confidence: 0.9 },
  denied_lines: {
    value: [
      {
        line_number: 1,
        hcpcs_code: "A0001",
        allowed_charge_amount: "120.00",
        payment_amount: "0.00",
        reason_category: "medical_necessity",
      },
      {
        line_number: 2,
        hcpcs_code: null,
        allowed_charge_amount: "5.00",
        payment_amount: "0.00",
        reason_category: "other",
      },
    ],
    confidence: 0.88,
  },
  total_allowed_charge_amount: { value: "120.00", confidence: 0.93 },
  total_payment_amount: { value: "0.00", confidence: 0.93 },
  denial_reason_category: { value: "medical_necessity", confidence: 0.91 },
  patient_name: { value: "Pat Example", confidence: 0.99 },
  member_id: { value: null, confidence: 0.4 },
  provider_name: { value: "Made-up Clinic", confidence: 0.96 },
  payer_name: { value: "Example Health Plan", confidence: 0.72 },
  reference_number: { value: "REF-0001", confidence: 0.9 },
  letter_date: { value: "2026-09-10", confidence: 0.98 },
  appeal_deadline: { value: "2026-12-10", confidence: 0.85 },
};

export const TRACE: AgentTrace = {
  end_state: "needs_review",
  extraction: { status: "done", prompt_version: "v1", text_sha256: "a".repeat(64) },
  rules: {
    status: "done",
    rules_version: "v1",
    today: "2026-10-20",
    amount_floor: "25.00",
    outcome: "pass",
    reasons: [],
  },
  scoring: {
    status: "done",
    feature_version: "v1",
    model_version: "v1",
    score: 0.634,
    threshold: 0.5,
  },
  retrieval: {
    status: "done",
    search_question_version: "v1",
    embedding_model: "made-up-embedder",
    min_similarity: 0.3,
    matches: [
      {
        source: "cms_ncd",
        source_document_id: "NCD-0.1",
        chunk_index: 3,
        text_sha256: "b".repeat(64),
        similarity: 0.8123,
      },
    ],
  },
  drafting: {
    status: "done",
    prompt_version: "v3",
    provider: "primary",
    model_name: "made-up-model",
    cited_paragraphs: 1,
    failure: null,
    problem_kinds: [],
    attempts: 1,
  },
  agent_version: "v5",
};

const POLICY_PARAGRAPH: LetterParagraph = {
  kind: "policy",
  text: "The made-up policy covers this service.",
  citations: [
    {
      source: "cms_ncd",
      source_document_id: "NCD-0.1",
      chunk_index: 3,
      text_sha256: "b".repeat(64),
      quote: "made-up quote that covers the service",
    },
  ],
};

export const ORIGINAL: AppealLetter = {
  paragraphs: [
    { kind: "other", text: "We ask you to look again at this made-up claim.", citations: [] },
    POLICY_PARAGRAPH,
  ],
};

export const EDITED: AppealLetter = {
  paragraphs: [
    { kind: "other", text: "Please review this made-up claim again.", citations: [] },
    POLICY_PARAGRAPH,
  ],
};

export const DRAFT: DraftDetail = {
  prompt_version: "v3",
  model_name: "made-up-model",
  original: ORIGINAL,
  latest_version: 1,
  latest: ORIGINAL,
  review_status: "pending",
  reviewed_by: null,
  reviewed_at: null,
  reject_reason: null,
  approved_version: null,
};

export const DETAIL: JobDetail = {
  job_id: 9,
  file_name: "letter-9.txt",
  file_type: "text",
  uploaded_at: "2026-10-17T10:00:00Z",
  uploaded_by: "ada@example.test",
  status: "done",
  failure: null,
  end_state: "needs_review",
  started_at: "2026-10-17T10:00:01Z",
  finished_at: "2026-10-17T10:00:20Z",
  cost_usd: "0.001141",
  text: "Made-up denial letter.\nThe claim MADE-UP-1001 is denied.",
  extraction: EXTRACTION,
  trace: TRACE,
  draft: DRAFT,
};
