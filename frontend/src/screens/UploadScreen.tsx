import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { canUpload } from "../auth/roles";
import { useSession } from "../auth/session";
import type { JobResponse } from "../api/types";
import { useNotify } from "../notify/notify";
import type { NotificationInput } from "../notify/notify";
import { ApiError } from "../api/client";
import { UPLOAD_MAX_BYTES, checkUploadFile } from "../jobs/uploadCheck";
import { isJobFinished, useJob, useUploadLetter } from "../jobs/useJob";
import { asSentence, describeError } from "./errorMessage";
import { FileDropZone } from "./FileDropZone";
import { formatFileSize } from "./fileSize";
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
      <div className="centered-screen">
        <section className="screen-card wide upload-card">
          <h1>Upload a denial letter</h1>
          <p className="error" role="alert">
            Your role cannot do this.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="centered-screen">
      <section className="screen-card wide upload-card">
        <h1>Upload a denial letter</h1>
        <p className="muted card-lead">
          A draft is written for a person to review. Nothing is sent to an insurer.
        </p>
        <UploadForm onUploaded={(id) => setSearchParams({ [JOB_PARAM]: String(id) })} />
        {jobId !== null && <JobStatusBox key={jobId} jobId={jobId} />}
      </section>
    </div>
  );
}

// A 429 here is a cap on a personal account's uploads (this month's, or today's for all
// individuals), not a login limit, so the screen shows the API's fixed sentence.
function uploadError(error: unknown): string {
  if (error instanceof ApiError && error.kind === "too_many") {
    return asSentence(error.message);
  }
  return describeError(error, { 413: "The file is larger than the upload limit." });
}

function UploadForm({ onUploaded }: { onUploaded: (jobId: number) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileProblem, setFileProblem] = useState<string | null>(null);
  const [noPersonalData, setNoPersonalData] = useState(false);
  const uploadMutation = useUploadLetter();
  const notify = useNotify();

  // A file the browser refuses is never kept, so it cannot be sent.
  function onPick(picked: File | null) {
    const problem = picked === null ? null : checkUploadFile(picked);
    setFileProblem(problem);
    setFile(problem === null ? picked : null);
    uploadMutation.reset();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null || !noPersonalData) {
      return;
    }
    uploadMutation.mutate(file, {
      onSuccess: (accepted) => {
        notify({
          tone: "success",
          title: "Letter uploaded",
          detail: `Job ${accepted.job_id} is being read.`,
        });
        onUploaded(accepted.job_id);
      },
    });
  }

  return (
    <form className="form" onSubmit={onSubmit}>
      <FileDropZone
        label="Denial letter"
        accept=".txt,.pdf,text/plain,application/pdf"
        hint={`.txt or .pdf with text, up to ${formatFileSize(UPLOAD_MAX_BYTES)}`}
        file={file}
        onPick={onPick}
      />
      <label className="checkbox">
        <input
          type="checkbox"
          checked={noPersonalData}
          onChange={(event) => setNoPersonalData(event.target.checked)}
        />
        I confirm this letter holds no real names, member ids, claim numbers or dates of birth
      </label>
      {fileProblem !== null && (
        <p className="error" role="alert">
          {fileProblem}
        </p>
      )}
      {uploadMutation.isError && (
        <p className="error" role="alert">
          {uploadError(uploadMutation.error)}
        </p>
      )}
      <button type="submit" disabled={file === null || !noPersonalData || uploadMutation.isPending}>
        {uploadMutation.isPending ? "Uploading…" : "Upload"}
      </button>
    </form>
  );
}

// The notification for a job that has just ended: a draft to review, another end, or a failure.
function finishedNotification(job: JobResponse): NotificationInput {
  const open = { label: "Open", to: `/app/jobs/${job.job_id}` };
  if (job.status === "failed") {
    return {
      tone: "error",
      title: "The letter could not be processed",
      detail: describeJob(job),
      action: open,
    };
  }
  if (job.end_state === "needs_review") {
    return { tone: "success", title: "Draft ready for review", action: open };
  }
  return {
    tone: "info",
    title: `Job ${job.job_id} finished`,
    detail: describeJob(job),
    action: open,
  };
}

function JobStatusBox({ jobId }: { jobId: number }) {
  const job = useJob(jobId);
  const notify = useNotify();
  // Whether the job was still running when it was last seen. A job already finished when the
  // screen opened (a reload) gets no notification; one that ends while it is watched gets one.
  const wasRunning = useRef<boolean | null>(null);

  useEffect(() => {
    if (job.data === undefined) {
      return;
    }
    const finished = isJobFinished(job.data);
    if (finished && wasRunning.current === true) {
      notify(finishedNotification(job.data));
    }
    wasRunning.current = !finished;
  }, [job.data, notify]);

  return (
    <section className="card-section" aria-label="Job status">
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
            <p className="links">
              <Link to={`/app/jobs/${jobId}`}>Open the denial</Link>
              <Link to="/app/worklist">Go to the worklist</Link>
            </p>
          ) : (
            <p className="muted">This page updates by itself.</p>
          )}
        </>
      )}
    </section>
  );
}
