// The account's overview, read when its screen is shown and again after a draft is approved or
// rejected (`refreshAfterReview`).

import { useQuery } from "@tanstack/react-query";

import { fetchOverview } from "../api/overview";

export function overviewKey() {
  return ["overview"] as const;
}

export function useOverview() {
  return useQuery({ queryKey: overviewKey(), queryFn: fetchOverview, retry: false });
}
