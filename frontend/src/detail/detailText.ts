// Plain words and numbers for the denial detail: field names, values, confidence, the rules'
// verdict and the evidence. The cut-off for "check this" is the browser's choice only.

import type {
  DenialLetterExtraction,
  DenialReasonCategory,
  DraftRejectReason,
  EvidenceSource,
  Extracted,
  ExtractedDeniedLine,
  LetterProblem,
  LetterProblemKind,
  ParagraphKind,
  RuleOutcome,
  RuleReason,
} from "../api/types";
import { DENIAL_REASON_TEXT } from "../screens/jobText";

// The version number of the model's own draft; a person's first edit is version 1. Matches
// `MODEL_DRAFT_VERSION` in `src/review/actions.py`.
export const MODEL_DRAFT_VERSION = 0;

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

// One fixed sentence per kind of a refused letter (`src/drafting/letter_check.py`). None repeats
// the letter's text.
export const LETTER_PROBLEM_TEXT: Readonly<Record<LetterProblemKind, string>> = {
  policy_without_citation: "a policy paragraph needs at least one citation",
  chunk_not_found: "the cited evidence no longer exists or has changed",
  quote_too_short: "the quote is too short to check",
  quote_not_in_evidence: "the quote is not in the cited evidence",
  placeholder_text: "the text still has a placeholder in square brackets",
  amount_not_a_denied_line: "a dollar amount is not the amount of a denied line",
  other_as_reason: 'the text gives the category "other" as the reason for the denial',
  edit_not_allowed:
    "this change is not allowed; only change text, or remove a paragraph or a citation",
};

// Where a problem is and what it is, counted from 1 for people: "Paragraph 2, citation 1: ...".
export function describeLetterProblem(problem: LetterProblem): string {
  const where =
    problem.citation_index === null
      ? `Paragraph ${problem.paragraph_index + 1}`
      : `Paragraph ${problem.paragraph_index + 1}, citation ${problem.citation_index + 1}`;
  return `${where}: ${LETTER_PROBLEM_TEXT[problem.kind]}.`;
}
