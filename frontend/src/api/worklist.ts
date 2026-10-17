// One page of the account's jobs, newest first, with keyset paging and two optional filters.

import { apiRequest } from "./client";
import type { DraftReviewStatus, JobStatus, WorklistPage } from "./types";

export interface WorklistFilters {
  status: JobStatus | null;
  reviewStatus: DraftReviewStatus | null;
}

export interface WorklistRequest extends WorklistFilters {
  // The `next_before` of the page before; null for the first page.
  before: number | null;
  limit: number;
}

// Builds `/worklist?...`; a filter or `before` that is not set is left out of the query.
export function worklistPath(request: WorklistRequest): string {
  const query = new URLSearchParams();
  if (request.before !== null) {
    query.set("before", String(request.before));
  }
  query.set("limit", String(request.limit));
  if (request.status !== null) {
    query.set("status", request.status);
  }
  if (request.reviewStatus !== null) {
    query.set("review_status", request.reviewStatus);
  }
  return `/worklist?${query.toString()}`;
}

export function fetchWorklist(request: WorklistRequest): Promise<WorklistPage> {
  return apiRequest<WorklistPage>(worklistPath(request));
}
