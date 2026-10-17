import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { SESSION_KEY } from "../src/auth/session";
import { jobKey } from "../src/jobs/useJob";
import { createQueryClient } from "../src/queryClient";
import { worklistKey } from "../src/worklist/useWorklist";
import { ADMIN } from "./helpers";

const ALL_JOBS = worklistKey({ status: null, reviewStatus: null });

function failWith(error: ApiError): () => Promise<never> {
  return () => Promise.reject(error);
}

describe("createQueryClient", () => {
  it("ends the session and empties the rest of the cache on a 401", async () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(SESSION_KEY, ADMIN);
    queryClient.setQueryData(ALL_JOBS, { pages: [], pageParams: [] });
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    await expect(
      queryClient.fetchQuery({
        queryKey: jobKey(8),
        queryFn: failWith(new ApiError(401, "unauthorized", "not logged in")),
        retry: false,
      }),
    ).rejects.toThrow();

    expect(queryClient.getQueryData(SESSION_KEY)).toBeNull();
    expect(queryClient.getQueryData(ALL_JOBS)).toBeUndefined();
    expect(queryClient.getQueryData(jobKey(7))).toBeUndefined();
  });

  it("empties the cache on a 401 from a mutation too", async () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(SESSION_KEY, ADMIN);
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    await expect(
      queryClient
        .getMutationCache()
        .build(queryClient, {
          mutationFn: failWith(new ApiError(401, "unauthorized", "not logged in")),
        })
        .execute(undefined),
    ).rejects.toThrow();

    expect(queryClient.getQueryData(SESSION_KEY)).toBeNull();
    expect(queryClient.getQueryData(jobKey(7))).toBeUndefined();
  });

  it("keeps the session and the cache on any other error", async () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(SESSION_KEY, ADMIN);
    queryClient.setQueryData(jobKey(7), { job_id: 7 });

    await expect(
      queryClient.fetchQuery({
        queryKey: jobKey(8),
        queryFn: failWith(new ApiError(403, "forbidden", "role not allowed")),
        retry: false,
      }),
    ).rejects.toThrow();

    expect(queryClient.getQueryData(SESSION_KEY)).toEqual(ADMIN);
    expect(queryClient.getQueryData(jobKey(7))).toEqual({ job_id: 7 });
  });
});
