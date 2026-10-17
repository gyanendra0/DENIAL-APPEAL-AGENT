// Shared test helpers: a stubbed `fetch` that answers per "METHOD /path", and the app rendered at
// a path with a memory router and a fresh cache. No test reaches a live API.

import { render } from "@testing-library/react";
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

// Stubs `fetch` with one answer per "METHOD /path"; a request with no answer fails the test.
export function stubApi(answers: Record<string, Answer>) {
  const fetchStub = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const answer = answers[key];
    if (answer === undefined) {
      throw new Error(`no stubbed answer for ${key}`);
    }
    return Promise.resolve(answer());
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

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

export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = createQueryClient();
  render(<App router={router} queryClient={queryClient} />);
  return { router, queryClient };
}
