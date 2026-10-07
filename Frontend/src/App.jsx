import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  ChevronsUpDown,
  CircleHelp,
  Database,
  FileBarChart2,
  LayoutDashboard,
  LogOut,
  Menu,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  X,
} from 'lucide-react';
import { createApiService } from './api.js';
import { demoService } from './lib/demo.js';
import { ACTIVE, WorkspaceContext, navigate } from './context.jsx';
import { Button, ErrorBox, Loading, Modal } from './components/UI.jsx';
import { Dashboard } from './pages/Dashboard.jsx';
import { History } from './pages/History.jsx';
import { Upload } from './pages/Upload.jsx';
import { Rules } from './pages/Rules.jsx';
import { JobDetails } from './pages/JobDetails.jsx';
import { Guide, Settings } from './pages/Guide.jsx';
import { Auth } from './pages/Auth.jsx';

const defaultDemo = import.meta.env.VITE_DATA_MODE !== 'api';
const navItems = [
  ['/', 'Overview', LayoutDashboard],
  ['/datasets', 'Datasets', Database],
  ['/rules', 'Rule sets', SlidersHorizontal],
  ['/reports', 'Reports', FileBarChart2],
];
function getRoute() {
  return window.location.hash.slice(1).split('?')[0] || '/';
}

export default function App() {
  const [mode, setMode] = useState(defaultDemo ? 'demo' : 'api');
  const [user, setUser] = useState(
    defaultDemo ? { email: 'demo@dataguard.local', name: 'Demo explorer' } : null,
  );
  const [token, setToken] = useState(null),
    [notice, setNotice] = useState(null);
  const [route, setRoute] = useState(getRoute),
    [mobileOpen, setMobileOpen] = useState(false),
    [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [jobs, setJobs] = useState([]),
    [rules, setRules] = useState([]),
    [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState(null),
    [revision, setRevision] = useState(0);
  const [search, setSearch] = useState(''),
    [toast, setToast] = useState(null),
    [activityOpen, setActivityOpen] = useState(false);
  const searchRef = useRef(null),
    toastTimer = useRef(null);
  const service = useMemo(
    () => (mode === 'demo' ? demoService : createApiService(token)),
    [mode, token],
  );
  const refresh = useCallback(() => setRevision((r) => r + 1), []);
  const notify = useCallback((message, type = 'success') => {
    clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 4500);
  }, []);
  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    setJobs([]);
    setRules([]);
    setNotice(null);
    setWorkspaceOpen(false);
    setActivityOpen(false);
    navigate('/');
  }, []);
  const handleError = useCallback(
    (e) => {
      if (e.status === 401) {
        logout();
        setNotice('Your session has expired. Sign in again to continue.');
      }
    },
    [logout],
  );
  useEffect(() => {
    const update = () => {
      setRoute(getRoute());
      setMobileOpen(false);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  useEffect(() => {
    const key = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === 'Escape') setMobileOpen(false);
    };
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('keydown', key);
      clearTimeout(toastTimer.current);
    };
  }, []);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    let timer;
    setLoading(true);
    setLoadError(null);
    async function load() {
      try {
        const [nextJobs, nextRules] = await Promise.all([
          service.listJobs(controller.signal),
          service.listRules(controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setJobs(nextJobs);
        setRules(nextRules);
        setLoading(false);
        if (nextJobs.some((j) => ACTIVE.includes(j.status))) timer = setTimeout(load, 3000);
      } catch (e) {
        if (!controller.signal.aborted) {
          setLoading(false);
          setLoadError(e);
          handleError(e);
        }
      }
    }
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [user, service, revision, handleError]);
  // Preserve mounted pages during background refresh so upload forms and report tabs keep their state.
  const initialLoading = loading && !jobs.length && !rules.length;
  const page = route.startsWith('/jobs/')
    ? 'Dataset report'
    : route === '/upload'
      ? 'Upload dataset'
      : navItems.find((n) => n[0] === route)?.[1] ||
        (route === '/settings' ? 'Settings' : 'Getting started');
  useEffect(() => {
    document.title = `${page} · DataGuard`;
  }, [page]);
  const context = {
    jobs,
    rules,
    service,
    user,
    mode,
    refresh,
    notify,
    handleError,
    search,
    setSearch,
    logout,
  };
  if (!user)
    return (
      <Auth
        notice={notice}
        onLogin={(t, u) => {
          setToken(t);
          setUser(u);
          setMode('api');
          navigate('/');
        }}
        onDemo={() => {
          setMode('demo');
          setUser({ email: 'demo@dataguard.local', name: 'Demo explorer' });
          setNotice(null);
          navigate('/');
        }}
      />
    );
  return (
    <WorkspaceContext.Provider value={context}>
      <div className="app-shell">
        {mobileOpen && (
          <button
            className="sidebar-scrim"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          />
        )}
        <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
          <a className="brand" href="#/">
            <span className="brand-mark">
              <ShieldCheck size={25} strokeWidth={1.8} />
            </span>
            DataGuard<span className="brand-period">.</span>
          </a>
          <button className="workspace-switch" onClick={() => setWorkspaceOpen(true)}>
            <span className="workspace-avatar">D</span>
            <span>
              <strong>{mode === 'demo' ? 'Demo workspace' : 'My workspace'}</strong>
              <small>{mode === 'demo' ? 'Personal · Demo plan' : 'Personal workspace'}</small>
            </span>
            <ChevronsUpDown size={14} />
          </button>
          <span className="nav-label">WORKSPACE</span>
          <nav aria-label="Main navigation">
            {navItems.map(([path, label, Icon]) => (
              <a
                href={`#${path}`}
                key={path}
                className={`nav-item ${(path === '/' ? route === '/' : route.startsWith(path) || (path === '/datasets' && (route.startsWith('/jobs') || route === '/upload'))) ? 'active' : ''}`}
                aria-current={route === path ? 'page' : undefined}
              >
                <Icon size={19} strokeWidth={1.7} />
                <span>{label}</span>
                {path === '/datasets' && <small>{jobs.length}</small>}
              </a>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="sidebar-tip">
              <span className="tip-icon">
                <Sparkles size={20} />
              </span>
              <h3>
                A little check.
                <br />A lot of confidence.
              </h3>
              <p>Your next great decision starts with clean data.</p>
              <button onClick={() => navigate('/upload')}>
                Validate a dataset
                <ArrowRight size={15} />
              </button>
            </div>
            <a className={`nav-item ${route === '/guide' ? 'active' : ''}`} href="#/guide">
              <CircleHelp size={18} />
              <span>Help & getting started</span>
              <span className="tiny-external">↗</span>
            </a>
            <a className={`nav-item ${route === '/settings' ? 'active' : ''}`} href="#/settings">
              <Settings2 size={18} />
              <span>Settings</span>
            </a>
            <button className="profile-button" onClick={() => navigate('/settings')}>
              <span className="profile-avatar">
                {mode === 'demo' ? 'DE' : user.email.slice(0, 2).toUpperCase()}
              </span>
              <span>
                <strong>{user.name || user.email.split('@')[0]}</strong>
                <small>{mode === 'demo' ? 'Personal workspace' : user.email}</small>
              </span>
              <ChevronsUpDown size={14} />
            </button>
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <div className="breadcrumbs">
              <button
                className="mobile-menu icon-button"
                aria-label="Open navigation"
                aria-expanded={mobileOpen}
                onClick={() => setMobileOpen(true)}
              >
                <Menu size={21} />
              </button>
              <span className="breadcrumb-workspace">Workspace</span>
              <span className="breadcrumb-divider">/</span>
              <strong>{page}</strong>
            </div>
            <div className="topbar-actions">
              <form
                className="global-search"
                onSubmit={(e) => {
                  e.preventDefault();
                  navigate('/datasets');
                }}
              >
                <Search size={16} />
                <input
                  ref={searchRef}
                  aria-label="Search workspace datasets"
                  placeholder="Search anything…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <kbd>Ctrl K</kbd>
              </form>
              <button
                className="notification-button"
                aria-label="View recent activity"
                onClick={() => setActivityOpen(true)}
              >
                <Bell size={19} />
                {jobs.length > 0 && <span />}
              </button>
              <span className="topbar-divider" />
              <button
                className="top-avatar"
                onClick={() => navigate('/settings')}
                aria-label="Open account settings"
              >
                {mode === 'demo' ? 'DE' : user.email.slice(0, 2).toUpperCase()}
              </button>
            </div>
          </header>
          <main className="main-content" id="main-content">
            {mode === 'demo' && (
              <div className="demo-banner">
                <span>
                  <span className="demo-dot" />
                  <strong>Demo workspace</strong>
                  <span className="demo-banner-detail">
                    Explore with synthetic data. Uploads stay in this browser session.
                  </span>
                </span>
                <button onClick={logout}>
                  Connect backend
                  <ArrowRight size={13} />
                </button>
              </div>
            )}
            {loadError && <ErrorBox error={loadError} onRetry={refresh} />}{' '}
            {initialLoading ? (
              <Loading />
            ) : (
              <>
                {route === '/' ? (
                  <Dashboard />
                ) : route === '/datasets' ? (
                  <History key="datasets" />
                ) : route === '/reports' ? (
                  <History key="reports" reports />
                ) : route === '/rules' ? (
                  <Rules />
                ) : route === '/upload' ? (
                  <Upload />
                ) : route.startsWith('/jobs/') ? (
                  <JobDetails key={route} id={route.slice(6)} />
                ) : route === '/guide' ? (
                  <Guide />
                ) : route === '/settings' ? (
                  <Settings />
                ) : (
                  <div className="empty">
                    <h1>This page took a wrong turn.</h1>
                    <Button onClick={() => navigate('/')}>Back to overview</Button>
                  </div>
                )}
              </>
            )}
          </main>
        </div>
        {toast && (
          <div className={`toast ${toast.type}`} role="status">
            <Check size={17} />
            {toast.message}
            <button aria-label="Dismiss notification" onClick={() => setToast(null)}>
              <X size={15} />
            </button>
          </div>
        )}
        {workspaceOpen && (
          <Modal title="Your workspace" onClose={() => setWorkspaceOpen(false)}>
            <div className="modal-body">
              <span className="workspace-dialog-icon">
                <ShieldCheck size={30} />
              </span>
              <h3>{mode === 'demo' ? 'Demo workspace' : 'Personal workspace'}</h3>
              <p className="muted">
                {mode === 'demo'
                  ? 'Explore the complete workflow with synthetic data. Uploaded files and custom rules reset when you reload the page.'
                  : `Your datasets and rule sets are scoped to ${user.email}.`}
              </p>
              <div className="modal-actions">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setWorkspaceOpen(false);
                    navigate('/settings');
                  }}
                >
                  Workspace settings
                </Button>
                <Button onClick={logout}>
                  {mode === 'demo' ? 'Connect backend' : 'Sign out'}
                  <ArrowRight size={15} />
                </Button>
              </div>
            </div>
          </Modal>
        )}
        {activityOpen && (
          <Modal title="Recent activity" onClose={() => setActivityOpen(false)}>
            <div className="modal-body activity-list">
              {jobs.length ? (
                jobs.slice(0, 5).map((job) => (
                  <button
                    key={job.job_id}
                    onClick={() => {
                      setActivityOpen(false);
                      navigate(`/jobs/${job.job_id}`);
                    }}
                  >
                    <span className="file-icon">
                      <Database size={18} />
                    </span>
                    <span>
                      <strong>{job.title}</strong>
                      <small>
                        {job.status.replaceAll('_', ' ').toLowerCase()} ·{' '}
                        {new Date(job.created_at).toLocaleDateString()}
                      </small>
                    </span>
                    <ArrowRight size={15} />
                  </button>
                ))
              ) : (
                <p className="muted">Upload your first dataset to start your activity history.</p>
              )}
            </div>
          </Modal>
        )}
      </div>
    </WorkspaceContext.Provider>
  );
}
