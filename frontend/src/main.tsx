import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import "./styles/global.css";

const queryClient = new QueryClient();

// Phase 1 exposes only the System Status page. Search, Ask, Catalog, Duplicates,
// Locations, Jobs, Errors, and Sync arrive in later phases (TECHSTACK 5.16).
const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [{ index: true, element: <SystemStatusPage /> }],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
