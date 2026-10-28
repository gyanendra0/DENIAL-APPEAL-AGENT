// The session is the answer of `GET /me`, kept in the query cache under one key. Login, the
// emailed code that confirms a sign-up and a Google sign-up write the user they get back into it;
// logout sets it to null. Each of them, and opening the login screen (where a 401 from any call
// leads), empties the rest of the cache, so no screen shows data of another user.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import {
  askPasswordReset,
  changePassword,
  confirmPasswordReset,
  fetchAuthOptions,
  fetchGoogleSignup,
  fetchMe,
  googleSignup,
  login,
  logout,
  resendCode,
  signup,
  verifyEmail,
} from "../api/auth";
import type {
  LoginInput,
  PasswordChangeInput,
  PasswordResetAskInput,
  PasswordResetConfirmInput,
  SignupInput,
  VerifyEmailInput,
} from "../api/auth";
import type { UserResponse } from "../api/types";

export const SESSION_KEY = ["me"] as const;
// What the server offers besides email and password; the same for every user.
export const AUTH_OPTIONS_KEY = ["auth-options"] as const;

const KEPT_KEYS: ReadonlySet<unknown> = new Set([SESSION_KEY[0], AUTH_OPTIONS_KEY[0]]);

// Removes every query but the session and the server's options. The other keys do not name the
// account, so data kept past a change of user would be shown to the next one.
export function clearAccountData(queryClient: QueryClient): void {
  queryClient.removeQueries({ predicate: (query) => !KEPT_KEYS.has(query.queryKey[0]) });
}

// Asked once per page load: only login, the code screen, a Google sign-up and logout change it,
// and they write it themselves. Without `staleTime` each screen that reads the session would ask
// `/me` again when it mounts.
export function useSession() {
  return useQuery({ queryKey: SESSION_KEY, queryFn: fetchMe, retry: false, staleTime: Infinity });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: LoginInput) => login(input),
    onSuccess: (user: UserResponse) => {
      clearAccountData(queryClient);
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

// A sign-up does not log in (the email is confirmed first), so the session is left as it is.
export function useSignup() {
  return useMutation({
    mutationFn: (input: SignupInput) => signup(input),
  });
}

// A right code logs in, as a login does.
export function useVerifyEmail() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: VerifyEmailInput) => verifyEmail(input),
    onSuccess: (user: UserResponse) => {
      clearAccountData(queryClient);
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

export function useResendCode() {
  return useMutation({ mutationFn: resendCode });
}

// The server's settings do not change while the page is open, so they are asked once per load.
export function useAuthOptions() {
  return useQuery({
    queryKey: AUTH_OPTIONS_KEY,
    queryFn: fetchAuthOptions,
    retry: false,
    staleTime: Infinity,
  });
}

// Asked when the Google "join" screen opens; a retry would not bring back an expired cookie.
export function useGoogleSignupInfo() {
  return useQuery({ queryKey: ["google-signup"], queryFn: fetchGoogleSignup, retry: false });
}

export function useGoogleSignup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => googleSignup(),
    onSuccess: (user: UserResponse) => {
      clearAccountData(queryClient);
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      // Known to be logged out: the guard sends the user to login without asking `/me` again.
      // Removing the session query instead would make the mounted screens fetch it at once.
      queryClient.setQueryData(SESSION_KEY, null);
      clearAccountData(queryClient);
    },
  });
}

// The user stays the same and the API sends a new cookie itself, so the cache is left as it is.
export function useChangePassword() {
  return useMutation({
    mutationFn: (input: PasswordChangeInput) => changePassword(input),
  });
}

// Asking for a reset code and setting the new password happen without a session; neither logs in,
// so the cache is left as it is (the login screen empties it when the user goes there).
export function useAskPasswordReset() {
  return useMutation({
    mutationFn: (input: PasswordResetAskInput) => askPasswordReset(input),
  });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationFn: (input: PasswordResetConfirmInput) => confirmPasswordReset(input),
  });
}
