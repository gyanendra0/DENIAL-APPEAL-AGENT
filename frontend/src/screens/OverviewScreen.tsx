import { Link } from "react-router";

import type { DraftReviewStatus, EndState, JobStatus, Overview } from "../api/types";
import { canUpload } from "../auth/roles";
import { useSession } from "../auth/session";
import { useOverview } from "../overview/useOverview";
import { describeError } from "./errorMessage";
import { END_STATE_TEXT, REVIEW_STATUS_TEXT, STATUS_TEXT } from "./jobText";
import { OverviewCharts } from "./OverviewCharts";
import { END_STATE_TONE, REVIEW_TONE, STATUS_TONE, percent, total } from "./overviewData";
import type { Tone } from "./overviewData";

// Shows only what `GET /overview` returns. Costs are the API's decimal strings, never added up or
// rounded here; the API sends them to admins only (null for everyone else), so their tiles are
// left out when they are null.
export function OverviewScreen() {
  const overview = useOverview();

  return (
    <>
      <h1>Overview</h1>
      <p className="muted">
        The jobs and drafts you can see so far: an admin sees the whole account, everyone else the
        letters they uploaded. Money at risk, win rate, payers and overdue appeals are not shown
        yet.
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
  const session = useSession();
  const letters = total(overview.jobs_by_status);

  if (letters === 0) {
    return (
      <section className="overview-empty">
        <p>Nothing uploaded yet. Upload a denial letter and its numbers show here.</p>
        {canUpload(session.data?.role) && (
          <Link className="button-link" to="/app/upload">
            Upload a denial letter
          </Link>
        )}
      </section>
    );
  }

  return (
    <>
      <StatTiles overview={overview} letters={letters} />
      {session.data?.role === "admin" && <OverviewCharts overview={overview} />}
      <div className="overview-grid">
        <CountCard<JobStatus>
          title="Jobs by status"
          counts={overview.jobs_by_status}
          labels={STATUS_TEXT}
          tones={STATUS_TONE}
        />
        <CountCard<EndState>
          title="Finished jobs by end state"
          counts={overview.jobs_by_end_state}
          labels={END_STATE_TEXT}
          tones={END_STATE_TONE}
        />
        <CountCard<DraftReviewStatus>
          title="Drafts by review"
          counts={overview.drafts_by_review_status}
          labels={REVIEW_STATUS_TEXT}
          tones={REVIEW_TONE}
        />
      </div>
    </>
  );
}

// The headline numbers. The counts are added up here; the costs never are (they are shown as
// the API sent them).
function StatTiles({ overview, letters }: { overview: Overview; letters: number }) {
  const reviews = overview.drafts_by_review_status;
  const decided = reviews.approved + reviews.rejected;
  const showsCost = overview.cost_usd_this_month !== null && overview.cost_usd_total !== null;

  return (
    <dl className="stat-tiles">
      <StatTile label="Letters uploaded" value={String(letters)} />
      <StatTile label="Drafts waiting for review" value={String(reviews.pending)} />
      <StatTile
        label="Approved"
        value={String(reviews.approved)}
        note={
          decided === 0 ? "None decided yet" : `${percent(reviews.approved, decided)}% of decided`
        }
      />
      <StatTile
        label="Average time to finish"
        value={
          overview.average_seconds === null ? "–" : `${overview.average_seconds.toFixed(1)} seconds`
        }
        note={overview.average_seconds === null ? "No finished job yet." : "From upload to the end"}
      />
      {showsCost && (
        <>
          <StatTile label="Model cost this month" value={`$${overview.cost_usd_this_month}`} />
          <StatTile label="Model cost in all" value={`$${overview.cost_usd_total}`} />
        </>
      )}
    </dl>
  );
}

function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat-tile">
      <dt>{label}</dt>
      <dd className="stat-value">{value}</dd>
      {note !== undefined && <dd className="muted stat-note">{note}</dd>}
    </div>
  );
}

interface CountCardProps<T extends string> {
  title: string;
  counts: Readonly<Record<T, number>>;
  labels: Readonly<Record<T, string>>;
  tones: Readonly<Record<T, Tone>>;
}

// One row per value, in the order of `labels`, so every value shows even when its count is 0.
// The bar under each label is decoration (its share of the card's total); the count is the
// table's cell, so a screen reader reads it.
function CountCard<T extends string>({ title, counts, labels, tones }: CountCardProps<T>) {
  const values = Object.keys(labels) as T[];
  const whole = values.reduce((sum, value) => sum + counts[value], 0);
  return (
    <section className="count-card">
      <h2>
        {title} <span className="muted count-card-total">{whole}</span>
      </h2>
      <table className="count-bars">
        <tbody>
          {values.map((value) => {
            const share = percent(counts[value], whole);
            return (
              <tr key={value} className={counts[value] === 0 ? "is-zero" : undefined}>
                <th scope="row">
                  {labels[value]}
                  <span className="count-track" aria-hidden="true">
                    <span
                      className={`count-fill tone-${tones[value]}`}
                      style={{ width: `${share}%` }}
                    />
                  </span>
                </th>
                <td className="count-value">{counts[value]}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
