import type { ChangeEvent } from "react";
import { Link, useSearchParams } from "react-router";

import type { DraftReviewStatus, JobStatus, WorklistItem } from "../api/types";
import type { WorklistFilters } from "../api/worklist";
import { canUpload } from "../auth/roles";
import { useSession } from "../auth/session";
import { useWorklist } from "../worklist/useWorklist";
import { describeError } from "./errorMessage";
import { DENIAL_REASON_TEXT, REVIEW_STATUS_TEXT, STATUS_TEXT, describeJob } from "./jobText";

// The filters are in the address (`?status=done&review_status=pending`), so a reload keeps them.
const STATUS_PARAM = "status";
const REVIEW_STATUS_PARAM = "review_status";

const JOB_STATUSES = Object.keys(STATUS_TEXT) as JobStatus[];
const REVIEW_STATUSES = Object.keys(REVIEW_STATUS_TEXT) as DraftReviewStatus[];

// A value that is not one of the allowed ones is ignored, as if the filter were not set.
function readChoice<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((choice) => choice === value) ?? null;
}

const UPLOADED_AT = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatChance(probability: number | null): string {
  return probability === null ? "–" : `${Math.round(probability * 100)}%`;
}

export function WorklistScreen() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: WorklistFilters = {
    status: readChoice(searchParams.get(STATUS_PARAM), JOB_STATUSES),
    reviewStatus: readChoice(searchParams.get(REVIEW_STATUS_PARAM), REVIEW_STATUSES),
  };

  function setFilter(name: string) {
    return (event: ChangeEvent<HTMLSelectElement>) => {
      const next = new URLSearchParams(searchParams);
      if (event.target.value === "") {
        next.delete(name);
      } else {
        next.set(name, event.target.value);
      }
      setSearchParams(next);
    };
  }

  return (
    <>
      <h1>Worklist</h1>
      <p className="muted">
        The account&apos;s uploads, newest first. Drafts are for a person to review; nothing is sent
        to an insurer.
      </p>
      <div className="filters">
        <label>
          Job status
          <select value={filters.status ?? ""} onChange={setFilter(STATUS_PARAM)}>
            <option value="">Any</option>
            {JOB_STATUSES.map((status) => (
              <option key={status} value={status}>
                {STATUS_TEXT[status]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Review status
          <select value={filters.reviewStatus ?? ""} onChange={setFilter(REVIEW_STATUS_PARAM)}>
            <option value="">Any</option>
            {REVIEW_STATUSES.map((status) => (
              <option key={status} value={status}>
                {REVIEW_STATUS_TEXT[status]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {filters.reviewStatus !== null && (
        <p className="muted">Jobs without a draft are left out while a review status is chosen.</p>
      )}
      <WorklistTable filters={filters} />
    </>
  );
}

function WorklistTable({ filters }: { filters: WorklistFilters }) {
  const session = useSession();
  const worklist = useWorklist(filters);

  if (worklist.isPending) {
    return <p>Loading…</p>;
  }
  // A failed first page leaves no data; a failed later page keeps the pages already shown.
  if (worklist.data === undefined) {
    return (
      <p className="error" role="alert">
        {describeError(worklist.error)}
      </p>
    );
  }

  const items = worklist.data.pages.flatMap((page) => page.items);
  if (items.length === 0) {
    return (
      <p>
        No jobs here yet.{" "}
        {canUpload(session.data?.role) && <Link to="/app/upload">Upload a denial letter</Link>}
      </p>
    );
  }

  return (
    <>
      <div className="table-scroll">
        <table className="worklist">
          <thead>
            <tr>
              <th scope="col">Job</th>
              <th scope="col">File</th>
              <th scope="col">Uploaded</th>
              <th scope="col">By</th>
              <th scope="col">Where it is</th>
              <th scope="col">Review</th>
              <th scope="col">Reason</th>
              <th scope="col">Win chance</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <WorklistRow key={item.job_id} item={item} />
            ))}
          </tbody>
        </table>
      </div>
      {worklist.isFetchNextPageError && (
        <p className="error" role="alert">
          {describeError(worklist.error)}
        </p>
      )}
      {worklist.hasNextPage && (
        <button
          type="button"
          onClick={() => void worklist.fetchNextPage()}
          disabled={worklist.isFetchingNextPage}
        >
          {worklist.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}

function WorklistRow({ item }: { item: WorklistItem }) {
  return (
    <tr>
      <td>
        <Link to={`/app/jobs/${item.job_id}`}>{item.job_id}</Link>
      </td>
      <td>{item.file_name}</td>
      <td>{UPLOADED_AT.format(new Date(item.uploaded_at))}</td>
      <td>{item.uploaded_by}</td>
      <td>{describeJob(item)}</td>
      <td>{item.review_status === null ? "–" : REVIEW_STATUS_TEXT[item.review_status]}</td>
      <td>{item.denial_reason === null ? "–" : DENIAL_REASON_TEXT[item.denial_reason]}</td>
      <td>{formatChance(item.win_probability)}</td>
    </tr>
  );
}
