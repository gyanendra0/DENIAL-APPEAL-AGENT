// The session is the answer of `GET /me`, kept in the query cache under one key. Login and sign-up
// write the user they get back into it; logout sets it to null and empties the rest of the cache,
// so no screen keeps data of the user who left.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe, login, logout, signup } from "../api/auth";
import type { LoginInput, SignupInput } from "../api/auth";
import type { UserResponse } from "../api/types";

export const SESSION_KEY = ["me"] as const;

// Asked once per page load: only login, sign-up and logout change it, and they write it themselves.
// Without `staleTime` each screen that reads the session would ask `/me` again when it mounts.
export function useSession() {
  return useQuery({ queryKey: SESSION_KEY, queryFn: fetchMe, retry: false, staleTime: Infinity });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: LoginInput) => login(input),
    onSuccess: (user: UserResponse) => {
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

export function useSignup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SignupInput) => signup(input),
    onSuccess: (user: UserResponse) => {
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
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== SESSION_KEY[0] });
    },
  });
}
