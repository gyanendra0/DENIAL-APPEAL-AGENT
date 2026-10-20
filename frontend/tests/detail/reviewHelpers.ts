// Opens the detail of job 9 for the draft review tests, as a reviewer unless told otherwise. Each read of the detail
// takes the next of `details` (the last one repeats), so a test sees what a refetch brings.

import { screen, within } from "@testing-library/react";

import type { JobDetail, JobResponse, UserResponse } from "../../src/api/types";
import { REVIEWER, jsonResponse, renderAt, stubApi } from "../helpers";
import type { Answer } from "../helpers";

const DONE_JOB: JobResponse = {
  job_id: 9,
  status: "done",
  failure: null,
  end_state: "needs_review",
  created_at: "2026-10-17T10:00:00Z",
  started_at: "2026-10-17T10:00:01Z",
  finished_at: "2026-10-17T10:00:20Z",
};

export function openDetail(
  details: JobDetail[],
  answers: Record<string, Answer> = {},
  user: UserResponse = REVIEWER,
) {
  let reads = 0;
  const fetchStub = stubApi({
    "GET /me": () => jsonResponse(200, user),
    "GET /jobs/9": () => jsonResponse(200, DONE_JOB),
    "GET /jobs/9/detail": () => jsonResponse(200, details[Math.min(reads++, details.length - 1)]),
    ...answers,
  });
  renderAt("/app/jobs/9");
  return fetchStub;
}

export function detailReads(fetchStub: ReturnType<typeof stubApi>): number {
  return fetchStub.mock.calls.filter(([path]) => path === "/jobs/9/detail").length;
}

export async function findDraft(): Promise<ReturnType<typeof within>> {
  return within(await screen.findByRole("region", { name: "Draft" }));
}
