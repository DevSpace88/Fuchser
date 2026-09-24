// App.tsx — defines the routes of the SPA.
//
// React Router decides, based on the URL, which component is displayed.
// The "Protected" routes are visible ONLY to logged-in users (or admins,
// respectively) — see the <RequireAuth> / <RequireAdmin> components below.

import { useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Menu, X as CloseIcon, LogOut, LayoutDashboard, Search, History as HistoryIcon, Shield } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { LoginPage } from "@/routes/login";
import { RegisterPage } from "@/routes/register";
import { DashboardPage } from "@/routes/dashboard";
import { AdminPage } from "@/routes/admin";
import { ResearchPage } from "@/routes/research";
import { ResearchDetailPage } from "@/routes/research_detail";
import { LandingPage } from "@/routes/landing";
import { HistoryPage } from "@/routes/history";
import { NotFoundPage } from "@/routes/not_found";
import { Button } from "@/components/ui/button";
import { FoxIcon } from "@/components/FoxIcon";

// ----------------------------------------------------------------------------
// SCHUTZ-WRAPPER (protection wrapper): redirects non-logged-in users to /login.
// ----------------------------------------------------------------------------
function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  // While we are still checking whether the user is logged in: render a
  // spinner (prevents flickering from "Login" -> "Dashboard").
  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  // Not logged in? Go to the login page, but we remember where the user
  // wanted to go (location) so we can forward them there after login.
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;

  return <>{children}</>;
}

// ----------------------------------------------------------------------------
// GAST-WRAPPER (guest wrapper): redirects already-logged-in users straight to /dashboard.
// (e.g. for /login and /register)
// ----------------------------------------------------------------------------
function RequireGuest({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (user) return <Navigate to="/dashboard" replace />;

  return <>{children}</>;
}

// Even stricter: admins only.
function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin")
    return (
      <div className="p-8">
        <h1 className="text-xl font-semibold">403 — kein Zugriff</h1>
        <p className="mt-2 text-muted-foreground">
          Diese Seite ist nur für Administratoren.
        </p>
      </div>
    );
  return <>{children}</>;
}
// ----------------------------------------------------------------------------
// Header (only visible when logged in).
// ----------------------------------------------------------------------------
function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  if (!user) return null;

  const handleLogout = async () => {
    setMobileMenuOpen(false);
    await logout();
    navigate("/", { replace: true });
  };

  const navLinks = [
    { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { to: "/research", label: "Research", icon: Search },
    { to: "/history", label: "Historie", icon: HistoryIcon },
    ...(user.role === "admin" ? [{ to: "/admin", label: "Admin", icon: Shield }] : []),
  ];

  return (
    <header className="sticky top-0 z-30 border-b border-border/80 bg-background/95 backdrop-blur-md">
      <div className="container flex h-14 items-center justify-between px-3 sm:px-6">
        {/* Desktop & Mobile Brand */}
        <div className="flex items-center gap-4">
          <Link to="/dashboard" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
            <FoxIcon size={22} className="text-primary shrink-0" />
            <span className="font-bold text-base tracking-tight hidden xs:inline text-foreground">Fuchser</span>
          </Link>

          {/* Desktop Nav */}
          <nav className="hidden md:flex items-center gap-1 text-sm font-medium">
            {navLinks.map(({ to, label, icon: Icon }) => {
              const active = location.pathname === to || (to !== "/dashboard" && location.pathname.startsWith(to));
              return (
                <Link
                  key={to}
                  to={to}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
                    active
                      ? "bg-primary/10 font-semibold text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span>{label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Desktop User Info & Logout */}
        <div className="hidden md:flex items-center gap-3 text-xs">
          <div className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-muted/30 px-2.5 py-1">
            <span className="max-w-[180px] truncate text-muted-foreground font-medium" title={user.email}>
              {user.email}
            </span>
            <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-bold text-primary uppercase">
              {user.role}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            className="h-8 gap-1 px-2.5 text-xs text-muted-foreground hover:text-destructive"
          >
            <LogOut className="h-3.5 w-3.5" />
            <span>Logout</span>
          </Button>
        </div>

        {/* Mobile Nav Trigger & Quick Links */}
        <div className="flex md:hidden items-center gap-1.5">
          <Link
            to="/research"
            className={`flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium transition-colors ${
              location.pathname.startsWith("/research")
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
            title="Research"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="text-[11px]">Chat</span>
          </Link>
          <Link
            to="/history"
            className={`flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium transition-colors ${
              location.pathname === "/history"
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
            title="Historie"
          >
            <HistoryIcon className="h-3.5 w-3.5" />
          </Link>
          <button
            onClick={() => setMobileMenuOpen((v) => !v)}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-border/70 text-foreground transition-colors hover:bg-muted"
            aria-label="Menü öffnen"
          >
            {mobileMenuOpen ? <CloseIcon className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {/* Mobile Menu Dropdown */}
      {mobileMenuOpen && (
        <div className="md:hidden border-t border-border/60 bg-card/98 px-4 py-3 shadow-xl backdrop-blur-lg animate-in slide-in-from-top-2 duration-150">
          <div className="mb-3 flex items-center justify-between border-b border-border/40 pb-2.5">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-foreground">{user.email}</p>
              <span className="inline-block rounded bg-primary/15 px-1.5 py-0.2 text-[10px] font-bold uppercase text-primary">
                {user.role}
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleLogout}
              className="h-7 gap-1 border-destructive/30 px-2 text-xs text-destructive hover:bg-destructive/10"
            >
              <LogOut className="h-3 w-3" />
              <span>Logout</span>
            </Button>
          </div>

          <nav className="grid grid-cols-2 gap-1.5">
            {navLinks.map(({ to, label, icon: Icon }) => {
              const active = location.pathname === to || (to !== "/dashboard" && location.pathname.startsWith(to));
              return (
                <Link
                  key={to}
                  to={to}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center gap-2 rounded-xl p-2.5 text-xs font-medium transition-colors ${
                    active
                      ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                      : "bg-muted/40 text-foreground hover:bg-muted"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span>{label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      )}
    </header>
  );
}

// ----------------------------------------------------------------------------
// Route definitions.
// ----------------------------------------------------------------------------
export default function App() {
  const { user, loading } = useAuth();
  return (
    <div className="min-h-screen bg-background">
      {user && <Header />}
      <Routes>
        <Route
          path="/login"
          element={
            <RequireGuest>
              <LoginPage />
            </RequireGuest>
          }
        />
        <Route
          path="/register"
          element={
            <RequireGuest>
              <RegisterPage />
            </RequireGuest>
          }
        />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <DashboardPage />
            </RequireAuth>
          }
        />
        <Route
          path="/research"
          element={
            <RequireAuth>
              <ResearchPage />
            </RequireAuth>
          }
        />
        <Route
          path="/history"
          element={
            <RequireAuth>
              <HistoryPage />
            </RequireAuth>
          }
        />
        <Route
          path="/research/:id"
          element={
            <RequireAuth>
              <ResearchDetailPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
        {/* Landing page for guests; logged-in users go straight to the dashboard. */}
        <Route
          path="/"
          element={
            loading ? (
              <div className="flex min-h-[50vh] items-center justify-center">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              </div>
            ) : user ? (
              <Navigate to="/dashboard" replace />
            ) : (
              <LandingPage />
            )
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </div>
  );
}
