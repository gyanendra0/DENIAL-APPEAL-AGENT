import { describe, expect, it } from "vitest";

import { fetchMe, login, logout, signup } from "../../src/api/auth";
import { ApiError } from "../../src/api/client";
import { ADMIN, jsonResponse, sentBody, stubApi } from "../helpers";

describe("auth calls", () => {
  it("signs up with a JSON body and returns the new user", async () => {
    const fetchStub = stubApi({ "POST /auth/signup": () => jsonResponse(201, ADMIN) });

    const user = await signup({
      account_name: "Made-up Clinic",
      email: ADMIN.email,
      password: "a long made-up password",
    });

    expect(user).toEqual(ADMIN);
    expect(sentBody(fetchStub, "POST /auth/signup")).toEqual({
      account_name: "Made-up Clinic",
      email: ADMIN.email,
      password: "a long made-up password",
    });
    expect(fetchStub.mock.calls[0]?.[1]?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("logs in with a JSON body and returns the user", async () => {
    const fetchStub = stubApi({ "POST /auth/login": () => jsonResponse(200, ADMIN) });

    await expect(login({ email: ADMIN.email, password: "pw" })).resolves.toEqual(ADMIN);
    expect(sentBody(fetchStub, "POST /auth/login")).toEqual({ email: ADMIN.email, password: "pw" });
  });

  it("logs out with a POST and no body", async () => {
    const fetchStub = stubApi({ "POST /auth/logout": () => new Response(null, { status: 204 }) });

    await expect(logout()).resolves.toBeUndefined();
    expect(fetchStub.mock.calls[0]?.[1]?.body).toBeUndefined();
  });
});

describe("fetchMe", () => {
  it("returns the logged-in user", async () => {
    stubApi({ "GET /me": () => jsonResponse(200, ADMIN) });

    await expect(fetchMe()).resolves.toEqual(ADMIN);
  });

  it("returns null when there is no session", async () => {
    stubApi({ "GET /me": () => jsonResponse(401, { detail: "not logged in" }) });

    await expect(fetchMe()).resolves.toBeNull();
  });

  it("raises any other error", async () => {
    stubApi({ "GET /me": () => jsonResponse(500, {}) });

    await expect(fetchMe()).rejects.toBeInstanceOf(ApiError);
  });
});
