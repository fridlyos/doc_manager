import { NavLink, Outlet } from "react-router-dom";
import { ProviderBadge } from "../components/ProviderBadge";

export function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <h1>doc_manager</h1>
        <ProviderBadge />
        <nav aria-label="Primary navigation">
          <NavLink to="/" end>
            Home
          </NavLink>
          <NavLink to="/status">Status</NavLink>
          <NavLink to="/tutorial">Tutorial</NavLink>
          <NavLink to="/locations">Locations</NavLink>
          <NavLink to="/ask">Ask</NavLink>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/documents">Documents</NavLink>
          <NavLink to="/duplicates">Duplicates</NavLink>
          <NavLink to="/coverage">Coverage</NavLink>
          <NavLink to="/sync-plans">Sync Plans</NavLink>
          <NavLink to="/errors">Errors</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
        </nav>
      </header>
      <main className="app-main" id="main">
        <Outlet />
      </main>
    </div>
  );
}
