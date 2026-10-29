import type { DraftReviewStatus, EndState, JobStatus } from "../api/types";

// What each value means, for the colour of its bar: good, waiting, bad, or neither. The label is
// always beside the bar, so the colour is never the only sign.
export type Tone = "good" | "waiting" | "bad" | "neutral";

export const STATUS_TONE: Readonly<Record<JobStatus, Tone>> = {
  queued: "neutral",
  running: "waiting",
  done: "good",
  failed: "bad",
};

export const END_STATE_TONE: Readonly<Record<EndState, Tone>> = {
  needs_review: "good",
  needs_review_no_draft: "waiting",
  stopped_by_rules: "neutral",
  below_threshold: "neutral",
  draft_failed: "bad",
};

export const REVIEW_TONE: Readonly<Record<DraftReviewStatus, Tone>> = {
  pending: "waiting",
  approved: "good",
  rejected: "bad",
};

export function total(counts: Readonly<Record<string, number>>): number {
  return Object.values(counts).reduce((sum, count) => sum + count, 0);
}

// A whole percent of `whole`; 0 when there is nothing to divide.
export function percent(part: number, whole: number): number {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}
