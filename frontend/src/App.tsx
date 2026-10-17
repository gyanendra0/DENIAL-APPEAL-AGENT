import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { RouterProvider } from "react-router";
import type { DataRouter } from "react-router";

// The router and the query client come from outside, so a test can give a memory router at any
// path and a fresh cache.
export function App({ router, queryClient }: { router: DataRouter; queryClient: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
