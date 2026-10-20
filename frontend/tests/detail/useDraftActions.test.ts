import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../src/api/client";
import {
  STALE_VERSION,
  isAlreadyDecided,
  isStaleVersion,
  refreshAfterReview,
} from "../../src/detail/useDraftActions";

describe("409 answers of a review action", () => {
  it("tells a newer version from a draft already final", () => {
    const stale = new ApiError(409, "conflict", STALE_VERSION);
    const decided = new ApiError(409, "conflict", "the draft was already approved or rejected");

    expect(isStaleVersion(stale)).toBe(true);
    expect(isAlreadyDecided(stale)).toBe(false);
    expect(isStaleVersion(decided)).toBe(false);
    expect(isAlreadyDecided(decided)).toBe(true);
  });

  it("treats any other error as neither", () => {
    const forbidden = new ApiError(403, "forbidden", STALE_VERSION);

    expect(isStaleVersion(forbidden)).toBe(false);
    expect(isAlreadyDecided(forbidden)).toBe(false);
    expect(isAlreadyDecided(new Error("boom"))).toBe(false);
  });
});

describe("refreshAfterReview", () => {
  it("marks the job's detail, every worklist page and the overview as stale", async () => {
    const queryClient = new QueryClient();
    const keys = [
      ["job-detail", 9],
      ["job-detail", 10],
      ["worklist", null, null],
      ["worklist", "done", "pending"],
      ["overview"],
      ["job", 9],
    ];
    for (const key of keys) {
      queryClient.setQueryData(key, {});
    }

    await refreshAfterReview(queryClient, 9);

    const stale = (key: unknown[]) => queryClient.getQueryState(key)?.isInvalidated;
    expect(stale(["job-detail", 9])).toBe(true);
    expect(stale(["worklist", null, null])).toBe(true);
    expect(stale(["worklist", "done", "pending"])).toBe(true);
    expect(stale(["overview"])).toBe(true);
    expect(stale(["job-detail", 10])).toBe(false);
    expect(stale(["job", 9])).toBe(false);
  });
});
