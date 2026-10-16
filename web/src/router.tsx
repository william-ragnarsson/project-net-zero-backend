import { createBrowserRouter, useParams } from 'react-router'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './routes/DashboardPage'
import { HistoryPage } from './routes/HistoryPage'
import { LandingPage } from './routes/LandingPage'
import { NewRunPage } from './routes/NewRunPage'
import { NotFoundPage } from './routes/NotFoundPage'
import { ProjectHistoryPage } from './routes/ProjectHistoryPage'
import { ReplayPage } from './routes/ReplayPage'
import { ReplayRedirect } from './routes/ReplayRedirect'
import { ReplaysPage } from './routes/ReplaysPage'
import { RouteError } from './routes/RouteError'
import { RunPage } from './routes/RunPage'
import { RunsPage } from './routes/RunsPage'
import { SettingsPage } from './routes/SettingsPage'
import { StoredRunPage } from './routes/StoredRunPage'

/** A new run id mounts a fresh page, so no state leaks from the run before. */
function KeyedRunPage() {
  const { runId = '' } = useParams()
  return <RunPage key={runId} />
}

/** Likewise a new replay id starts a fresh replay. */
function KeyedReplayPage() {
  const { replayId = '' } = useParams()
  return <ReplayPage key={replayId} />
}

/** And a stored run from the history. */
function KeyedStoredRunPage() {
  const { runId = '' } = useParams()
  return <StoredRunPage key={runId} />
}

export const router = createBrowserRouter([
  // the front door, outside the shell
  { path: '/', element: <LandingPage />, errorElement: <RouteError /> },
  {
    element: <AppShell />,
    children: [
      {
        // inside the shell, so a page that throws keeps the sidebar
        errorElement: <RouteError />,
        children: [
          { path: 'dashboard', element: <DashboardPage /> },
          { path: 'new', element: <NewRunPage /> },
          { path: 'runs', element: <RunsPage /> },
          { path: 'runs/:runId', element: <KeyedRunPage /> },
          { path: 'replays', element: <ReplaysPage /> },
          { path: 'replays/:replayId', element: <KeyedReplayPage /> },
          // the old `/replay?src=` links
          { path: 'replay', element: <ReplayRedirect /> },
          { path: 'history', element: <HistoryPage /> },
          { path: 'history/:projectId', element: <ProjectHistoryPage /> },
          { path: 'history/:projectId/runs/:runId', element: <KeyedStoredRunPage /> },
          { path: 'settings', element: <SettingsPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
])
