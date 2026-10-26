import { Navigate, createBrowserRouter } from "react-router";
import type { RouteObject } from "react-router";

import { RequireSession } from "./auth/RequireSession";
import { HOME_PATH } from "./auth/redirect";
import { AccountScreen } from "./screens/AccountScreen";
import { AppLayout } from "./screens/AppLayout";
import { JobDetailScreen } from "./screens/JobDetailScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { OverviewScreen } from "./screens/OverviewScreen";
import { SignupScreen } from "./screens/SignupScreen";
import { UploadScreen } from "./screens/UploadScreen";
import { WorklistScreen } from "./screens/WorklistScreen";

// Every screen is under `/app/...`, so no screen path equals an API path the dev proxy forwards.
export const routes: RouteObject[] = [
  { path: "/app/login", element: <LoginScreen /> },
  { path: "/app/signup", element: <SignupScreen /> },
  {
    path: "/app",
    element: (
      <RequireSession>
        <AppLayout />
      </RequireSession>
    ),
    children: [
      { index: true, element: <Navigate to={HOME_PATH} replace /> },
      { path: "worklist", element: <WorklistScreen /> },
      { path: "upload", element: <UploadScreen /> },
      { path: "jobs/:id", element: <JobDetailScreen /> },
      { path: "overview", element: <OverviewScreen /> },
      { path: "account", element: <AccountScreen /> },
    ],
  },
  { path: "*", element: <Navigate to={HOME_PATH} replace /> },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
