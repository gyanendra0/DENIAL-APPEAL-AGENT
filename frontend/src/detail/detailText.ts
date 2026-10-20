// Plain words and numbers for the denial detail: field names, values, confidence, the rules'
// verdict and the evidence. The cut-off for "check this" is the browser's choice only.

import type {
  DenialLetterExtraction,
  DenialReasonCategory,
  DraftRejectReason,
  EvidenceSource,
  Extracted,
  ExtractedDeniedLine,
  ParagraphKind,
  RuleOutcome,
  RuleReason,
} from "../api/types";
import { DENIAL_REASON_TEXT } from "../screens/jobText";

// A field read with less confidence than this, or not found at all, is marked "check this".
export const LOW_CONFIDENCE = 0.8;

export function needsCheck(field: Extracted<unknown>): boolean {
  return isEmpty(field.value) || field.confidence < LOW_CONFIDENCE;
}

function isEmpty(value: unknown): boolean {
  return value === null || (Array.isArray(value) && value.length === 0);
}

// Every extracted field, in the order the API gives them.
export const FIELD_LABELS: Readonly<Record<keyof DenialLetterExtraction, string>> = {
  claim_number: "Claim number",
  service_from_date: "Service from",
  service_thru_date: "Service through",
  diagnosis_codes: "Diagnosis codes",
  denied_lines: "Denied lines",
  total_allowed_charge_amount: "Total allowed charge",
  total_payment_amount: "Total payment",
  denial_reason_category: "Denial reason",
  patient_name: "Patient name",
  member_id: "Member ID",
  provider_name: "Provider",
  payer_name: "Payer",
  reference_number: "Reference number",
  letter_date: "Letter date",
  appeal_deadline: "Appeal deadline",
};

export const FIELD_NAMES = Object.keys(FIELD_LABELS) as (keyof DenialLetterExtraction)[];

function describeDeniedLine(line: ExtractedDeniedLine): string {
  const code = line.hcpcs_code ?? "no code";
  return (
    `Line ${line.line_number} (${code}): allowed $${line.allowed_charge_amount}, ` +
    `paid $${line.payment_amount}, ${DENIAL_REASON_TEXT[line.reason_category].toLowerCase()}`
  );
}

// One field's value as text, one entry per line for a list; "Not found" when empty.
export function formatFieldValue(
  name: keyof DenialLetterExtraction,
  extraction: DenialLetterExtraction,
): string {
  if (name === "denied_lines") {
    const lines = extraction.denied_lines.value;
    return lines === null || lines.length === 0
      ? "Not found"
      : lines.map(describeDeniedLine).join("\n");
  }
  if (name === "diagnosis_codes") {
    const codes = extraction.diagnosis_codes.value;
    return codes === null || codes.length === 0 ? "Not found" : codes.join(", ");
  }
  if (name === "denial_reason_category") {
    const reason: DenialReasonCategory | null = extraction.denial_reason_category.value;
    return reason === null ? "Not found" : DENIAL_REASON_TEXT[reason];
  }
  const value = extraction[name].value;
  if (value === null) {
    return "Not found";
  }
  return name === "total_allowed_charge_amount" || name === "total_payment_amount"
    ? `$${value}`
    : value;
}

// A share from 0 to 1 as a whole percentage.
export function formatPercent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

export const RULE_OUTCOME_TEXT: Readonly<Record<RuleOutcome, string>> = {
  pass: "Passed the rules",
  must_review: "A person must review it",
  hard_fail: "Stopped by the rules",
};

export const RULE_REASON_TEXT: Readonly<Record<RuleReason, string>> = {
  deadline_not_after_letter_date: "The appeal deadline is not after the letter date.",
  deadline_missing: "The letter gives no appeal deadline.",
  deadline_passed: "The appeal deadline has passed.",
  amount_missing: "The letter gives no total allowed charge.",
  amount_zero: "The total allowed charge is zero.",
  amount_below_floor: "The total allowed charge is below the amount worth appealing.",
};

export const SOURCE_TEXT: Readonly<Record<EvidenceSource, string>> = {
  cms_ncd: "CMS coverage determination",
  cms_manual: "CMS manual",
};

export const PARAGRAPH_KIND_TEXT: Readonly<Record<ParagraphKind, string>> = {
  policy: "Policy argument",
  other: "Other",
};

// The rubric's reasons (`src/evaluation/review_rubric.py` REJECT_REASON_MEANINGS), shortened.
export const REJECT_REASON_TEXT: Readonly<Record<DraftRejectReason, string>> = {
  wrong_fact: "A fact differs from the denial letter",
  no_policy_argument: "No real argument for overturning the denial",
  citation_does_not_fit: "A cited quote does not support its paragraph",
  poor_wording: "Too many sentences need rewording",
  other: "Another reason",
};
