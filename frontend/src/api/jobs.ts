// The upload of one denial letter and the status of the job that drafts it.

import { apiRequest } from "./client";
import type { JobResponse, UploadAccepted } from "./types";

// Sends the file as the multipart field `file`, with `no_personal_data=true`: the screen calls this
// only after the user has confirmed the letter holds no real personal details, and the API refuses
// an upload without it. The browser sets the `Content-Type` with its boundary and a plain
// `Content-Length` itself; the API refuses a body without one (411).
export function uploadLetter(file: File): Promise<UploadAccepted> {
  const body = new FormData();
  body.append("file", file);
  body.append("no_personal_data", "true");
  return apiRequest<UploadAccepted>("/uploads", { method: "POST", body });
}

export function fetchJob(jobId: number): Promise<JobResponse> {
  return apiRequest<JobResponse>(`/jobs/${jobId}`);
}
