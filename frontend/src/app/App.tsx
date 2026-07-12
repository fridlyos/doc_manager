import { Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>doc_manager</h1>
        <span className="app-badge">local</span>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
