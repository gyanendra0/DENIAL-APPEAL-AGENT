import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { createQueryClient } from "./queryClient";
import { createAppRouter } from "./router";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("the page has no #root element");
}
createRoot(root).render(
  <StrictMode>
    <App router={createAppRouter()} queryClient={createQueryClient()} />
  </StrictMode>,
);
