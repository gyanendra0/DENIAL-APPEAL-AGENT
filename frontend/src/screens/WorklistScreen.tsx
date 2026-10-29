import type { ChangeEvent, ReactNode } from "react";
import { Link, useSearchParams } from "react-router";

import type { DraftReviewStatus, JobStatus, WorklistItem } from "../api/types";
import type { WorklistFilters } from "../api/worklist";
import { canUpload } from "../auth/roles";
import { useSession } from "../auth/session";
import { useWorklist } from "../worklist/useWorklist";
import { describeError } from "./errorMessage";
import { FileIcon } from "./icons";
import { DENIAL_REASON_TEXT, REVIEW_STATUS_TEXT, STATUS_TEXT, describeJob } from "./jobText";
import { END_STATE_TONE, REVIEW_TONE } from "./overviewData";
import type { Tone } from "./overviewData";

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

// A win chance's band, for the colour of its bar: likely, possible, or unlikely.
const LIKELY_CHANCE = 0.6;
const POSSIBLE_CHANCE = 0.3;

function chanceTone(probability: number): Tone {
  if (probability >= LIKELY_CHANCE) {
    return "good";
  }
  return probability >= POSSIBLE_CHANCE ? "waiting" : "bad";
}

// Where a job is, for the colour of its pill: a finished job takes its end state's tone.
function jobTone(item: WorklistItem): Tone {
  if (item.status === "failed") {
    return "bad";
  }
  if (item.status === "done") {
    return item.end_state === null ? "good" : END_STATE_TONE[item.end_state];
  }
  return "waiting";
}

function jobCount(count: number, more: boolean, filtered: boolean): string {
  const jobs = `${count} ${count === 1 ? "job" : "jobs"}`;
  if (more) {
    return `${jobs} so far`;
  }
  return filtered ? `${jobs} shown` : jobs;
}

export function WorklistScreen() {
  const session = useSession();
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

  function clearFilters() {
    const next = new URLSearchParams(searchParams);
    next.delete(STATUS_PARAM);
    next.delete(REVIEW_STATUS_PARAM);
    setSearchParams(next);
  }

  return (
    <>
      <div className="page-title">
        <h1>Worklist</h1>
        {canUpload(session.data?.role) && (
          <Link className="button-link" to="/app/upload">
            Upload a denial letter
          </Link>
        )}
      </div>
      <p className="muted">
        The account&apos;s uploads, newest first. Drafts are for a person to review; nothing is sent
        to an insurer.
      </p>
      <WorklistTable filters={filters} onClearFilters={clearFilters}>
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
      </WorklistTable>
    </>
  );
}

interface WorklistTableProps {
  filters: WorklistFilters;
  onClearFilters: () => void;
  // The filter controls, shown in the toolbar beside the job count.
  children: ReactNode;
}

function WorklistTable({ filters, onClearFilters, children }: WorklistTableProps) {
  const session = useSession();
  const worklist = useWorklist(filters);
  const filtered = filters.status !== null || filters.reviewStatus !== null;
  const items = worklist.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="worklist-toolbar">
        {children}
        {worklist.data !== undefined && (
          <p className="muted worklist-count" role="status">
            {jobCount(items.length, worklist.hasNextPage, filtered)}
          </p>
        )}
        {filtered && (
          <button type="button" className="link-button" onClick={onClearFilters}>
            Clear filters
          </button>
        )}
      </div>
      {/* Under the filters it is about, not after the table. */}
      {filters.reviewStatus !== null && (
        <p className="muted worklist-note">
          Jobs without a draft are left out while a review status is chosen.
        </p>
      )}
      {worklist.isPending && <p>Loading…</p>}
      {/* A failed first page leaves no data; a failed later page keeps the pages already shown. */}
      {!worklist.isPending && worklist.data === undefined && (
        <p className="error" role="alert">
          {describeError(worklist.error)}
        </p>
      )}
      {worklist.data !== undefined && items.length === 0 && (
        <section className="worklist-empty">
          {filtered ? (
            <>
              <p>No jobs match these filters.</p>
              <button type="button" className="secondary" onClick={onClearFilters}>
                Clear filters
              </button>
            </>
          ) : (
            <>
              <p>No jobs here yet.</p>
              {canUpload(session.data?.role) && (
                <Link className="button-link" to="/app/upload">
                  Upload your first letter
                </Link>
              )}
            </>
          )}
        </section>
      )}
      {items.length > 0 && (
        <div className="table-scroll worklist-card">
          <table className="worklist">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Uploaded</th>
                <th scope="col">Where it is</th>
                <th scope="col">Review</th>
                <th scope="col">Reason</th>
                <th scope="col">Win chance</th>
                <th scope="col" className="worklist-edit">
                  Edit
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <WorklistRow key={item.job_id} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {worklist.isFetchNextPageError && (
        <p className="error" role="alert">
          {describeError(worklist.error)}
        </p>
      )}
      {worklist.hasNextPage && (
        <button
          type="button"
          className="secondary load-more"
          onClick={() => void worklist.fetchNextPage()}
          disabled={worklist.isFetchingNextPage}
        >
          {worklist.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}

// The text is always in the pill; the dot's colour only adds to it.
function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="pill">
      <span className={`pill-dot tone-${tone}`} aria-hidden="true" />
      {children}
    </span>
  );
}

function WinChance({ probability }: { probability: number | null }) {
  if (probability === null) {
    return <span className="muted">–</span>;
  }
  const share = Math.round(probability * 100);
  return (
    <span className="chance">
      <span className="chance-track" aria-hidden="true">
        <span
          className={`chance-fill tone-${chanceTone(probability)}`}
          style={{ width: `${share}%` }}
        />
      </span>
      {share}%
    </span>
  );
}

function WorklistRow({ item }: { item: WorklistItem }) {
  return (
    <tr>
      <td>
        <span className="worklist-file">
          <FileIcon />
          <span>
            <strong>{item.file_name}</strong>
            <span className="muted worklist-sub">#{item.job_id}</span>
          </span>
        </span>
      </td>
      <td>
        {UPLOADED_AT.format(new Date(item.uploaded_at))}
        <span className="muted worklist-sub">{item.uploaded_by}</span>
      </td>
      <td>
        <Pill tone={jobTone(item)}>{describeJob(item)}</Pill>
      </td>
      <td>
        {item.review_status === null ? (
          <span className="muted">–</span>
        ) : (
          <Pill tone={REVIEW_TONE[item.review_status]}>
            {REVIEW_STATUS_TEXT[item.review_status]}
          </Pill>
        )}
      </td>
      <td>
        {item.denial_reason === null ? (
          <span className="muted">–</span>
        ) : (
          <span className="tag">{DENIAL_REASON_TEXT[item.denial_reason]}</span>
        )}
      </td>
      <td>
        <WinChance probability={item.win_probability} />
      </td>
      <td className="worklist-edit">
        <Link
          className="button-link secondary"
          to={`/app/jobs/${item.job_id}`}
          aria-label={`Edit ${item.file_name}`}
        >
          Edit
        </Link>
      </td>
    </tr>
  );
}
