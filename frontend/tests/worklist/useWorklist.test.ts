import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { WorklistItem } from "../../src/api/types";
import { createQueryClient } from "../../src/queryClient";
import { WORKLIST_PAGE_SIZE, useWorklist, worklistKey } from "../../src/worklist/useWorklist";
import { jsonResponse, stubApi } from "../helpers";

function item(jobId: number): WorklistItem {
  return {
    job_id: jobId,
    file_name: `letter-${jobId}.txt`,
    uploaded_at: "2026-10-17T10:00:00Z",
    uploaded_by: "ada@example.test",
    status: "done",
    failure: null,
    end_state: "needs_review",
    review_status: "pending",
    denial_reason: "noncovered",
    win_probability: 0.5,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  return createElement(QueryClientProvider, { client: createQueryClient() }, children);
}

const NO_FILTERS = { status: null, reviewStatus: null };

describe("worklistKey", () => {
  it("keys the pages by both filters", () => {
    expect(worklistKey({ status: "done", reviewStatus: null })).toEqual(["worklist", "done", null]);
  });
});

describe("useWorklist", () => {
  it("asks for the next page with the last page's next_before and stops after the last", async () => {
    const fetchStub = stubApi({
      [`GET /worklist?limit=${WORKLIST_PAGE_SIZE}`]: () =>
        jsonResponse(200, { items: [item(9), item(8)], next_before: 8 }),
      [`GET /worklist?before=8&limit=${WORKLIST_PAGE_SIZE}`]: () =>
        jsonResponse(200, { items: [item(3)], next_before: null }),
    });

    const { result } = renderHook(() => useWorklist(NO_FILTERS), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(false);
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("sends the filters with every page", async () => {
    const fetchStub = stubApi({
      [`GET /worklist?limit=${WORKLIST_PAGE_SIZE}&status=done&review_status=approved`]: () =>
        jsonResponse(200, { items: [], next_before: null }),
    });

    const { result } = renderHook(() => useWorklist({ status: "done", reviewStatus: "approved" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });
});
