import { useParams } from "react-router";

import { ApiError } from "../api/client";
import type { JobDetail } from "../api/types";
import { useSession } from "../auth/session";
import { AnalysisSection } from "../detail/AnalysisSection";
import { DraftView } from "../detail/DraftView";
import { ExtractionTable } from "../detail/ExtractionTable";
import { isJobFinished, useJob } from "../jobs/useJob";
import { useJobDetail } from "../jobs/useJobDetail";
import { describeError } from "./errorMessage";
import { describeJob } from "./jobText";

const JOB_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

function readJobId(value: string | undefined): number | null {
  if (value === undefined || !/^[1-9][0-9]*$/.test(value)) {
    return null;
  }
  return Number(value);
}

// Another account's job answers 404 like a missing one, so both read "Not found."
function NotFound() {
  return (
    <>
      <h1>Denial</h1>
      <p className="error" role="alert">
        Not found.
      </p>
    </>
  );
}

// The split view of one denial: the letter and what the agent read from it on the left, the
// appeal draft on the right.
export function JobDetailScreen() {
  const jobId = readJobId(useParams().id);
  if (jobId === null) {
    return <NotFound />;
  }
  return <JobDetailView key={jobId} jobId={jobId} />;
}

function JobDetailView({ jobId }: { jobId: number }) {
  // Polls while the job runs; the detail is read once it is finished.
  const job = useJob(jobId);
  const finished = job.data !== undefined && isJobFinished(job.data);
  const detail = useJobDetail(jobId, finished);

  const error = job.error ?? detail.error;
  if (error instanceof ApiError && error.kind === "not_found") {
    return <NotFound />;
  }
  if (error !== null) {
    return (
      <>
        <h1>Denial</h1>
        <p className="error" role="alert">
          {describeError(error)}
        </p>
      </>
    );
  }
  if (job.data === undefined) {
    return <p>Loading…</p>;
  }
  if (!finished) {
    return (
      <>
        <h1>Job {jobId}</h1>
        <p role="status">{describeJob(job.data)}</p>
        <p className="muted">This page updates by itself.</p>
      </>
    );
  }
  if (detail.data === undefined) {
    return <p>Loading…</p>;
  }
  return <SplitView detail={detail.data} />;
}

function SplitView({ detail }: { detail: JobDetail }) {
  const session = useSession();

  return (
    <>
      <h1>
        Job {detail.job_id}: {detail.file_name}
      </h1>
      {session.data?.account_kind === "personal" && (
        <p className="notice">
          This is an estimate, not legal or medical advice. Nothing is sent to your insurer; you
          decide what to do with the letter.
        </p>
      )}
      <div className="split">
        <div>
          <section aria-label="Letter">
            <h2>Letter</h2>
            {detail.file_type === "pdf" && <p className="muted">Text read from the PDF.</p>}
            <pre className="letter-text">{detail.text}</pre>
          </section>
          <JobFacts detail={detail} />
          <ExtractionTable extraction={detail.extraction} />
          <AnalysisSection trace={detail.trace} />
        </div>
        <div className="appeal-pane">
          <DraftView jobId={detail.job_id} draft={detail.draft} endState={detail.end_state} />
        </div>
      </div>
    </>
  );
}

function JobFacts({ detail }: { detail: JobDetail }) {
  return (
    <section aria-label="Job">
      <h2>Job</h2>
      <dl className="facts">
        <dt>Where it is</dt>
        <dd>{describeJob(detail)}</dd>
        <dt>Uploaded</dt>
        <dd>
          {JOB_TIME.format(new Date(detail.uploaded_at))} by {detail.uploaded_by}
        </dd>
        {detail.started_at !== null && (
          <>
            <dt>Started</dt>
            <dd>{JOB_TIME.format(new Date(detail.started_at))}</dd>
          </>
        )}
        {detail.finished_at !== null && (
          <>
            <dt>Finished</dt>
            <dd>{JOB_TIME.format(new Date(detail.finished_at))}</dd>
          </>
        )}
        <dt>Model cost</dt>
        <dd>${detail.cost_usd}</dd>
      </dl>
    </section>
  );
}
