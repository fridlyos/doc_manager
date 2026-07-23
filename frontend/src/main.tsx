import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { JobsPage } from "./pages/JobsPage";
import { LocationsPage } from "./pages/LocationsPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { ErrorsPage } from "./pages/ErrorsPage";
import { SearchPage } from "./pages/SearchPage";
import "./styles/global.css";

const queryClient = new QueryClient();

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <SystemStatusPage /> },
      { path: "locations", element: <LocationsPage /> },
      { path: "search", element: <SearchPage /> },
      { path: "documents", element: <DocumentsPage /> },
      { path: "errors", element: <ErrorsPage /> },
      { path: "jobs", element: <JobsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
