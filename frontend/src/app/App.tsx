import { NavLink, Outlet } from "react-router-dom";
import { ProviderBadge } from "../components/ProviderBadge";

// Navigation grouped into modules so the sidebar reads as distinct areas of the
// app rather than a flat list. Each group is a titled section.
const NAV_GROUPS: { title: string; items: { to: string; label: string; end?: boolean }[] }[] = [
  {
    title: "Overview",
    items: [
      { to: "/", label: "Home", end: true },
      { to: "/tutorial", label: "Tutorial" },
      { to: "/status", label: "System status" },
    ],
  },
  {
    title: "Library",
    items: [
      { to: "/locations", label: "Locations" },
      { to: "/documents", label: "Documents" },
      { to: "/coverage", label: "Coverage" },
      { to: "/duplicates", label: "Duplicates" },
      { to: "/sync-plans", label: "Sync Plans" },
    ],
  },
  {
    title: "Ask & Search",
    items: [
      { to: "/search", label: "Search" },
      { to: "/ask", label: "Ask" },
    ],
  },
  {
    title: "Operations",
    items: [
      { to: "/jobs", label: "Jobs" },
      { to: "/errors", label: "Errors" },
    ],
  },
];

export function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <aside className="sidebar">
        {/* Brand doubles as the always-available route back to the main screen. */}
        <NavLink to="/" end className="sidebar-brand">
          <span className="sidebar-brand-name">doc_manager</span>
          <ProviderBadge />
        </NavLink>
        <nav aria-label="Primary navigation">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title}>
              <h2>{group.title}</h2>
              <ul>
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavLink to={item.to} end={item.end}>
                      {item.label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>
      <main className="app-main" id="main">
        <Outlet />
      </main>
    </div>
  );
}
