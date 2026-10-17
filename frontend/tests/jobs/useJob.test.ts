import { describe, expect, it } from "vitest";

import type { JobStatus } from "../../src/api/types";
import { isJobFinished, jobKey } from "../../src/jobs/useJob";

function job(status: JobStatus) {
  return {
    job_id: 1,
    status,
    failure: null,
    end_state: null,
    created_at: "2026-10-17T10:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

describe("isJobFinished", () => {
  it.each([
    ["queued", false],
    ["running", false],
    ["done", true],
    ["failed", true],
  ] as const)("says a %s job finished: %s", (status, finished) => {
    expect(isJobFinished(job(status))).toBe(finished);
  });
});

describe("jobKey", () => {
  it("keys each job by its id", () => {
    expect(jobKey(7)).toEqual(["job", 7]);
  });
});
