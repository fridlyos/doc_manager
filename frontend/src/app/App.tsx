import { NavLink, Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>doc_manager</h1>
        <span className="app-badge">local</span>
        <nav aria-label="Primary navigation">
          <NavLink to="/">Status</NavLink>
          <NavLink to="/locations">Locations</NavLink>
          <NavLink to="/ask">Ask</NavLink>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/documents">Documents</NavLink>
          <NavLink to="/errors">Errors</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
