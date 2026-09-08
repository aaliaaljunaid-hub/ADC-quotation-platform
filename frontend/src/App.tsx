import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useAccount } from "./context/AccountContext";
import Login from "./pages/Login";
import Catalog from "./pages/Catalog";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import QuoteBuilder from "./pages/QuoteBuilder";
import Branding from "./pages/Branding";
import EstimationAids from "./pages/EstimationAids";
import Team from "./pages/Team";
import PublicQuote from "./pages/PublicQuote";

function Shell() {
  const { account, logout } = useAccount();
  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="brand">{account?.firm_name}</span>
        <nav>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/catalog">Catalog</NavLink>
          <NavLink to="/estimation-aids">Estimation Aids</NavLink>
          <NavLink to="/branding">Branding</NavLink>
          <NavLink to="/team">Team</NavLink>
        </nav>
        <button className="link-button" onClick={logout}>Log out</button>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/quotes/:quoteId" element={<QuoteBuilder />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/estimation-aids" element={<EstimationAids />} />
          <Route path="/branding" element={<Branding />} />
          <Route path="/team" element={<Team />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function AuthedApp() {
  const { account, loading } = useAccount();

  if (loading) return <div className="centered">Loading...</div>;
  if (!account) return <Login />;
  return <Shell />;
}

export default function App() {
  return (
    <Routes>
      {/* Public: no login required — the share_token itself is the credential. */}
      <Route path="/quote/:shareToken" element={<PublicQuote />} />
      <Route path="*" element={<AuthedApp />} />
    </Routes>
  );
}
