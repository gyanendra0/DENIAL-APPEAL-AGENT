import { describe, expect, it } from "vitest";

import type { DenialLetterExtraction } from "../../src/api/types";
import {
  FIELD_LABELS,
  FIELD_NAMES,
  LETTER_PROBLEM_TEXT,
  LOW_CONFIDENCE,
  describeLetterProblem,
  formatFieldValue,
  formatPercent,
  needsCheck,
} from "../../src/detail/detailText";
import { EXTRACTION } from "./fixtures";

describe("needsCheck", () => {
  it("marks a field read with less confidence than the cut-off", () => {
    expect(LOW_CONFIDENCE).toBe(0.8);
    expect(needsCheck({ value: "C-1", confidence: 0.79 })).toBe(true);
    expect(needsCheck({ value: "C-1", confidence: 0.8 })).toBe(false);
  });

  it("marks a field that was not found, even when sure of it", () => {
    expect(needsCheck({ value: null, confidence: 0.99 })).toBe(true);
    expect(needsCheck({ value: [], confidence: 0.99 })).toBe(true);
  });
});

describe("formatFieldValue", () => {
  it("writes each kind of value as plain text", () => {
    expect(formatFieldValue("claim_number", EXTRACTION)).toBe("MADE-UP-1001");
    expect(formatFieldValue("total_allowed_charge_amount", EXTRACTION)).toBe("$120.00");
    expect(formatFieldValue("diagnosis_codes", EXTRACTION)).toBe("Z00.00, R51.9");
    expect(formatFieldValue("denial_reason_category", EXTRACTION)).toBe("Medical necessity");
    expect(formatFieldValue("denied_lines", EXTRACTION)).toBe(
      "Line 1 (A0001): allowed $120.00, paid $0.00, medical necessity\n" +
        "Line 2 (no code): allowed $5.00, paid $0.00, other",
    );
  });

  it("says when a value was not found", () => {
    const empty: DenialLetterExtraction = {
      ...EXTRACTION,
      member_id: { value: null, confidence: 0.2 },
      diagnosis_codes: { value: [], confidence: 0.5 },
      denied_lines: { value: null, confidence: 0.1 },
      denial_reason_category: { value: null, confidence: 0.1 },
    };
    expect(formatFieldValue("member_id", empty)).toBe("Not found");
    expect(formatFieldValue("diagnosis_codes", empty)).toBe("Not found");
    expect(formatFieldValue("denied_lines", empty)).toBe("Not found");
    expect(formatFieldValue("denial_reason_category", empty)).toBe("Not found");
  });

  it("has a label for every field the API sends", () => {
    expect(FIELD_NAMES).toEqual(Object.keys(EXTRACTION));
    expect(FIELD_NAMES.every((name) => FIELD_LABELS[name].length > 0)).toBe(true);
  });
});

describe("formatPercent", () => {
  it("rounds a share to a whole percentage", () => {
    expect(formatPercent(0.634)).toBe("63%");
    expect(formatPercent(0.5)).toBe("50%");
    expect(formatPercent(1)).toBe("100%");
  });
});

describe("describeLetterProblem", () => {
  it("names the paragraph counted from 1", () => {
    expect(
      describeLetterProblem({
        kind: "policy_without_citation",
        paragraph_index: 1,
        citation_index: null,
      }),
    ).toBe("Paragraph 2: a policy paragraph needs at least one citation.");
  });

  it("names the citation counted from 1 when the problem is one citation's", () => {
    expect(
      describeLetterProblem({
        kind: "quote_not_in_evidence",
        paragraph_index: 0,
        citation_index: 2,
      }),
    ).toBe("Paragraph 1, citation 3: the quote is not in the cited evidence.");
  });

  it("has a sentence for every kind the letter check sends", () => {
    expect(Object.keys(LETTER_PROBLEM_TEXT).sort()).toEqual([
      "amount_not_a_denied_line",
      "chunk_not_found",
      "edit_not_allowed",
      "other_as_reason",
      "placeholder_text",
      "policy_without_citation",
      "quote_not_in_evidence",
      "quote_too_short",
    ]);
  });
});
