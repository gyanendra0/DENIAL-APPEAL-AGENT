// The auth calls: sign-up, login, logout and the logged-in user. The API sets and clears the
// HttpOnly login cookie itself; these calls never see the token.

import { ApiError, apiRequest, jsonRequest } from "./client";
import type { UserResponse } from "./types";

export interface SignupInput {
  account_name: string;
  email: string;
  password: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export function signup(input: SignupInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/signup", jsonRequest("POST", input));
}

export function login(input: LoginInput): Promise<UserResponse> {
  return apiRequest<UserResponse>("/auth/login", jsonRequest("POST", input));
}

export async function logout(): Promise<void> {
  await apiRequest<undefined>("/auth/logout", { method: "POST" });
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
