// The auth calls: sign-up, the emailed code that confirms it, login, logout, the logged-in user, a
// password change, a reset and the "Continue with Google" sign-up. The API sets and clears the
// HttpOnly login cookie itself; these calls never see the token.

import { ApiError, apiRequest, jsonRequest } from "./client";
import type { AuthOptions, GoogleSignupInfo, UserResponse } from "./types";

// "Continue with Google" is a link to this API path, not a `fetch`: the browser itself goes on to
// Google and comes back.
export const GOOGLE_START_PATH = "/auth/google/start";

export interface SignupInput {
  account_name: string;
  email: string;
  password: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export interface VerifyEmailInput {
  code: string;
}

export interface PasswordChangeInput {
  current_password: string;
  new_password: string;
}

export interface PasswordResetAskInput {
  email: string;
}

export interface PasswordResetConfirmInput {
  email: string;
  code: string;
  new_password: string;
}

export interface GoogleSignupInput {
  account_name: string;
}

// Answers 201 with the new user but does not log in: the API sets its own signed cookie for the
// code screen and emails a 6-digit code.
export function signup(input: SignupInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/signup", jsonRequest("POST", input));
}

// The user comes from the API's code-screen cookie, never from the body. A right code logs in.
export function verifyEmail(input: VerifyEmailInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/verify-email", jsonRequest("POST", input));
}

// Answers 202; the new code makes the older one useless. The body is not read.
export async function resendCode(): Promise<void> {
  await apiRequest<unknown>("/auth/verify-email/resend", { method: "POST" });
}

export function login(input: LoginInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/login", jsonRequest("POST", input));
}

export async function logout(): Promise<void> {
  await apiRequest<undefined>("/auth/logout", { method: "POST" });
}

// Answers 204 and a new login cookie: this browser stays logged in, every other session ends.
export async function changePassword(input: PasswordChangeInput): Promise<void> {
  await apiRequest<undefined>("/me/password", jsonRequest("POST", input));
}

// Answers 202 and the same sentence whether or not the email has an account, so the body is not
// read: the screen says it in its own words.
export async function askPasswordReset(input: PasswordResetAskInput): Promise<void> {
  await apiRequest<unknown>("/auth/password-reset", jsonRequest("POST", input));
}

// Answers 204 and no cookie: the user logs in with the new password. Every other session ends.
export async function confirmPasswordReset(input: PasswordResetConfirmInput): Promise<void> {
  await apiRequest<undefined>("/auth/password-reset/confirm", jsonRequest("POST", input));
}

export function fetchAuthOptions(): Promise<AuthOptions> {
  return apiRequest<AuthOptions>("/auth/options");
}

// The email of the pending Google sign-up, or null when there is none or it has expired (a 401).
// The API reads it from its own signed cookie, so the email never travels in a URL.
export async function fetchGoogleSignup(): Promise<GoogleSignupInfo | null> {
  try {
    return await apiRequest<GoogleSignupInfo>("/auth/google/signup");
  } catch (error) {
    if (error instanceof ApiError && error.kind === "unauthorized") {
      return null;
    }
    throw error;
  }
}

// Creates the account with this name, its admin (no password) and the Google link, and logs in.
export function googleSignup(input: GoogleSignupInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/google/signup", jsonRequest("POST", input));
}

// The logged-in user, or null when there is no session (a 401). Any other error is raised.
export async function fetchMe(): Promise<UserResponse | null> {
  try {
    return await apiRequest<UserResponse>("/me");
  } catch (error) {
    if (error instanceof ApiError && error.kind === "unauthorized") {
      return null;
    }
    throw error;
  }
}
