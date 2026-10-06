import { createBrowserRouter } from 'react-router'
import { AppShell } from './components/AppShell'
import { LandingPage } from './routes/LandingPage'
import { NotFoundPage } from './routes/NotFoundPage'
import { ReplayPage } from './routes/ReplayPage'
import { RouteError } from './routes/RouteError'
import { RunPage } from './routes/RunPage'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      {
        // inside the shell, so a page that throws keeps the header
        errorElement: <RouteError />,
        children: [
          { index: true, element: <LandingPage /> },
          { path: 'runs/:runId', element: <RunPage /> },
          { path: 'replay', element: <ReplayPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
])
