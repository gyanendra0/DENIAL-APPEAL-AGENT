import type { DraftReviewStatus, EndState, JobStatus, Overview } from "../api/types";
import { useOverview } from "../overview/useOverview";
import { describeError } from "./errorMessage";
import { END_STATE_TEXT, REVIEW_STATUS_TEXT, STATUS_TEXT } from "./jobText";

// Shows only what `GET /overview` returns. Costs are the API's decimal strings, never added up or
// rounded here.
export function OverviewScreen() {
  const overview = useOverview();

  return (
    <>
      <h1>Overview</h1>
      <p className="muted">
        The account&apos;s jobs and drafts so far. Money at risk, win rate, payers and overdue
        appeals are not shown yet.
      </p>
      {overview.isPending && <p>Loading…</p>}
      {overview.isError && (
        <p className="error" role="alert">
          {describeError(overview.error)}
        </p>
      )}
      {overview.data !== undefined && <OverviewBody overview={overview.data} />}
    </>
  );
}

function OverviewBody({ overview }: { overview: Overview }) {
  return (
    <>
      <CountTable<JobStatus>
        title="Jobs by status"
        counts={overview.jobs_by_status}
        labels={STATUS_TEXT}
      />
      <CountTable<EndState>
        title="Finished jobs by end state"
        counts={overview.jobs_by_end_state}
        labels={END_STATE_TEXT}
      />
      <CountTable<DraftReviewStatus>
        title="Drafts by review"
        counts={overview.drafts_by_review_status}
        labels={REVIEW_STATUS_TEXT}
      />
      <h2>Cost and time</h2>
      <dl className="facts">
        <dt>Model cost this month</dt>
        <dd>${overview.cost_usd_this_month}</dd>
        <dt>Model cost in all</dt>
        <dd>${overview.cost_usd_total}</dd>
        <dt>Average time from upload to the end of the job</dt>
        <dd>
          {overview.average_seconds === null
            ? "No finished job yet."
            : `${overview.average_seconds.toFixed(1)} seconds`}
        </dd>
      </dl>
    </>
  );
}

interface CountTableProps<T extends string> {
  title: string;
  counts: Readonly<Record<T, number>>;
  labels: Readonly<Record<T, string>>;
}

// One row per value, in the order of `labels`, so every value shows even when its count is 0.
function CountTable<T extends string>({ title, counts, labels }: CountTableProps<T>) {
  const values = Object.keys(labels) as T[];
  return (
    <section>
      <h2>{title}</h2>
      <div className="table-scroll">
        <table className="counts">
          <tbody>
            {values.map((value) => (
              <tr key={value}>
                <th scope="row">{labels[value]}</th>
                <td>{counts[value]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
