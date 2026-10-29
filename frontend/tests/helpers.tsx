// Shared test helpers: a stubbed `fetch` that answers per "METHOD /path", and the app rendered at
// a path with a memory router and a fresh cache. No test reaches a live API.

import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { vi } from "vitest";

import { App } from "../src/App";
import type { UserResponse } from "../src/api/types";
import { createQueryClient } from "../src/queryClient";
import { routes } from "../src/router";

export const ADMIN: UserResponse = {
  user_id: 1,
  email: "ada@example.test",
  role: "admin",
  account_id: 10,
  account_name: "Made-up Clinic",
  account_kind: "shared",
};

export const REVIEWER: UserResponse = { ...ADMIN, user_id: 2, role: "reviewer" };
export const VIEWER: UserResponse = { ...ADMIN, user_id: 3, role: "viewer" };
// An individual: admin of their own personal account, whose name is made up and never shown.
export const INDIVIDUAL: UserResponse = {
  user_id: 4,
  email: "lin@individual.test",
  role: "admin",
  account_id: 20,
  account_name: "personal-0123456789abcdef",
  account_kind: "personal",
};

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// A fresh Response for each call, since a body can be read only once. A rejected promise stands
// for no answer at all.
export type Answer = () => Response | Promise<Response>;

// Requests a test made with no stubbed answer. `apiRequest` turns the thrown error into a network
// error the screen may show quietly, so `expectEveryRequestAnswered` (run after each test) fails
// the test instead.
const unanswered: string[] = [];

export function expectEveryRequestAnswered() {
  const missing = unanswered.splice(0);
  if (missing.length > 0) {
    throw new Error(`no stubbed answer for ${missing.join(", ")}`);
  }
}

// The login and sign-up screens ask which ways in the server offers; unless a test says otherwise,
// Google sign-in is off, so those screens show only the email and password form.
const GOOGLE_OFF: Record<string, Answer> = {
  "GET /auth/options": () => jsonResponse(200, { google: false }),
};

// Stubs `fetch` with one answer per "METHOD /path"; a request with no answer fails the test.
export function stubApi(answers: Record<string, Answer>) {
  const known = { ...GOOGLE_OFF, ...answers };
  const fetchStub = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const answer = known[key];
    if (answer === undefined) {
      unanswered.push(key);
      throw new Error(`no stubbed answer for ${key}`);
    }
    return Promise.resolve(answer());
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

// An empty sidebar "Recent" list, which every logged-in screen asks for.
export const NO_RECENT: Record<string, Answer> = {
  "GET /worklist?limit=10": () => jsonResponse(200, { items: [], next_before: null }),
};

// An empty first worklist page, for a test that lands on the worklist but is about something else.
export const EMPTY_WORKLIST: Record<string, Answer> = {
  ...NO_RECENT,
  "GET /worklist?limit=50": () => jsonResponse(200, { items: [], next_before: null }),
};

// The JSON body sent with the first request to "METHOD /path".
export function sentBody(fetchStub: ReturnType<typeof stubApi>, key: string): unknown {
  const call = fetchStub.mock.calls.find(([input, init]) => {
    return `${init?.method ?? "GET"} ${String(input)}` === key;
  });
  if (call === undefined) {
    throw new Error(`no request to ${key}`);
  }
  return JSON.parse(String(call[1]?.body));
}

// The notifications at the top of the page, to look for one by its words.
export function notifications() {
  return within(screen.getByLabelText("Notifications"));
}

export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = createQueryClient();
  render(<App router={router} queryClient={queryClient} />);
  return { router, queryClient };
}
