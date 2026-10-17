import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { canUpload } from "../auth/roles";
import { useSession } from "../auth/session";
import { isJobFinished, useJob, useUploadLetter } from "../jobs/useJob";
import { describeError } from "./errorMessage";
import { describeJob } from "./jobText";

// The job id in the address (`?job=12`), so a reload keeps showing the same job.
const JOB_PARAM = "job";

function readJobId(value: string | null): number | null {
  if (value === null || !/^[1-9][0-9]*$/.test(value)) {
    return null;
  }
  return Number(value);
}

export function UploadScreen() {
  const session = useSession();
  const [searchParams, setSearchParams] = useSearchParams();
  const jobId = readJobId(searchParams.get(JOB_PARAM));

  if (!canUpload(session.data?.role)) {
    return (
      <>
        <h1>Upload a denial letter</h1>
        <p className="error" role="alert">
          Your role cannot do this.
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Upload a denial letter</h1>
      <p className="muted">
        A text file or a PDF with text. A draft is written for a person to review; nothing is sent
        to an insurer.
      </p>
      <UploadForm onUploaded={(id) => setSearchParams({ [JOB_PARAM]: String(id) })} />
      {jobId !== null && <JobStatusBox key={jobId} jobId={jobId} />}
    </>
  );
}

function UploadForm({ onUploaded }: { onUploaded: (jobId: number) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const uploadMutation = useUploadLetter();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null) {
      return;
    }
    uploadMutation.mutate(file, { onSuccess: (accepted) => onUploaded(accepted.job_id) });
  }

  return (
    <form className="form" onSubmit={onSubmit}>
      <label>
        Denial letter
        <input
          type="file"
          accept=".txt,.pdf,text/plain,application/pdf"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      {uploadMutation.isError && (
        <p className="error" role="alert">
          {describeError(uploadMutation.error, {
            413: "The file is larger than the upload limit.",
          })}
        </p>
      )}
      <button type="submit" disabled={file === null || uploadMutation.isPending}>
        {uploadMutation.isPending ? "Uploading…" : "Upload"}
      </button>
    </form>
  );
}

function JobStatusBox({ jobId }: { jobId: number }) {
  const job = useJob(jobId);

  return (
    <section className="status-box" aria-label="Job status">
      <h2>Job {jobId}</h2>
      {job.isPending && <p>Loading…</p>}
      {job.isError && (
        <p className="error" role="alert">
          {describeError(job.error)}
        </p>
      )}
      {job.data !== undefined && (
        <>
          <p role="status">{describeJob(job.data)}</p>
          {isJobFinished(job.data) ? (
            <Link to="/app/worklist">Go to the worklist</Link>
          ) : (
            <p className="muted">This page updates by itself.</p>
          )}
        </>
      )}
    </section>
  );
}
