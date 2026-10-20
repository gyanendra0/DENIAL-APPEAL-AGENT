// The account's overview: job and draft counts, costs and the average job time.

import { apiRequest } from "./client";
import type { Overview } from "./types";

export function fetchOverview(): Promise<Overview> {
  return apiRequest<Overview>("/overview");
}
