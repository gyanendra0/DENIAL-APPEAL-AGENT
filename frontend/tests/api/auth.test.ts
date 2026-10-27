import { describe, expect, it } from "vitest";

import {
  askPasswordReset,
  changePassword,
  confirmPasswordReset,
  fetchMe,
  login,
  logout,
  signup,
} from "../../src/api/auth";
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

  it("changes the password with a JSON body and accepts the 204", async () => {
    const fetchStub = stubApi({ "POST /me/password": () => new Response(null, { status: 204 }) });
    const input = { current_password: "made-up current", new_password: "a long made-up password" };

    await expect(changePassword(input)).resolves.toBeUndefined();
    expect(sentBody(fetchStub, "POST /me/password")).toEqual(input);
  });

  it("raises the API's 400 for a wrong current password", async () => {
    stubApi({
      "POST /me/password": () => jsonResponse(400, { detail: "the current password is wrong" }),
    });

    await expect(
      changePassword({ current_password: "wrong", new_password: "a long made-up password" }),
    ).rejects.toMatchObject({ status: 400, message: "the current password is wrong" });
  });
});

describe("password reset calls", () => {
  it("asks for a reset link with the email and accepts the 202", async () => {
    const fetchStub = stubApi({
      "POST /auth/password-reset": () =>
        jsonResponse(202, { detail: "if the email belongs to an account, a link is on its way" }),
    });

    await expect(askPasswordReset({ email: ADMIN.email })).resolves.toBeUndefined();
    expect(sentBody(fetchStub, "POST /auth/password-reset")).toEqual({ email: ADMIN.email });
  });

  it("sets the new password with the token and accepts the 204", async () => {
    const fetchStub = stubApi({
      "POST /auth/password-reset/confirm": () => new Response(null, { status: 204 }),
    });
    const input = { token: "a.made-up.token", new_password: "a long made-up password" };

    await expect(confirmPasswordReset(input)).resolves.toBeUndefined();
    expect(sentBody(fetchStub, "POST /auth/password-reset/confirm")).toEqual(input);
  });

  it("raises the API's 400 for a link that is not valid", async () => {
    stubApi({
      "POST /auth/password-reset/confirm": () =>
        jsonResponse(400, { detail: "the reset link is not valid or has expired" }),
    });

    await expect(
      confirmPasswordReset({ token: "made-up", new_password: "a long made-up password" }),
    ).rejects.toMatchObject({ status: 400 });
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
