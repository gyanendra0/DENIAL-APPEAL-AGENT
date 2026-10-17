// The upload of one denial letter and the status of the job that drafts it.

import { apiRequest } from "./client";
import type { JobResponse, UploadAccepted } from "./types";

// Sends the file as the multipart field `file`. The browser sets the `Content-Type` with its
// boundary and a plain `Content-Length` itself; the API refuses a body without one (411).
export function uploadLetter(file: File): Promise<UploadAccepted> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<UploadAccepted>("/uploads", { method: "POST", body });
}

export function fetchJob(jobId: number): Promise<JobResponse> {
  return apiRequest<JobResponse>(`/jobs/${jobId}`);
}
