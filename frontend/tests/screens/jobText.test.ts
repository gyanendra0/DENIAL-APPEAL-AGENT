import { describe, expect, it } from "vitest";

import type { EndState, JobFailure, JobResponse } from "../../src/api/types";
import {
  DENIAL_REASON_TEXT,
  END_STATE_TEXT,
  FAILURE_TEXT,
  REVIEW_STATUS_TEXT,
  describeJob,
} from "../../src/screens/jobText";

const BASE: JobResponse = {
  job_id: 1,
  status: "queued",
  failure: null,
  end_state: null,
  created_at: "2026-10-17T10:00:00Z",
  started_at: null,
  finished_at: null,
};

const END_STATES: EndState[] = [
  "needs_review",
  "needs_review_no_draft",
  "stopped_by_rules",
  "below_threshold",
  "draft_failed",
];
const FAILURES: JobFailure[] = [
  "extraction_failed",
  "budget_exceeded",
  "llm_unavailable",
  "evidence_not_ready",
  "interrupted",
  "internal_error",
];

describe("describeJob", () => {
  it("gives the status of an unfinished job", () => {
    expect(describeJob(BASE)).toBe("Waiting to start");
    expect(describeJob({ ...BASE, status: "running" })).toBe("Reading the letter and drafting");
  });

  it.each(END_STATES)("gives the end state %s of a done job", (endState) => {
    expect(describeJob({ ...BASE, status: "done", end_state: endState })).toBe(
      END_STATE_TEXT[endState],
    );
  });

  it.each(FAILURES)("gives the failure %s of a failed job", (failure) => {
    const line = describeJob({ ...BASE, status: "failed", failure });
    expect(line).toBe(`Failed: ${FAILURE_TEXT[failure].toLowerCase()}`);
  });

  it("falls back to the status when the end state or failure is missing", () => {
    expect(describeJob({ ...BASE, status: "done" })).toBe("Finished");
    expect(describeJob({ ...BASE, status: "failed" })).toBe("Failed");
  });
});

describe("the text maps", () => {
  it("name every review status and denial reason in plain words", () => {
    expect(Object.keys(REVIEW_STATUS_TEXT)).toEqual(["pending", "approved", "rejected"]);
    expect(Object.keys(DENIAL_REASON_TEXT)).toEqual([
      "noncovered",
      "medical_necessity",
      "duplicate",
      "benefits_exhausted",
      "coordination_of_benefits",
      "other",
    ]);
    for (const text of [
      ...Object.values(REVIEW_STATUS_TEXT),
      ...Object.values(DENIAL_REASON_TEXT),
    ]) {
      expect(text).not.toMatch(/_/);
    }
  });
});
