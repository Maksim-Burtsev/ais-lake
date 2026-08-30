import { AppShell } from './components/AppShell';
import { MapCanvas } from './components/MapCanvas';
import { StoryPage } from './components/StoryPage';

/** Two pages, one bundle. /ship/{slug}-{mmsi} arrives server-rendered (F31) and
 *  this mount replaces that markup with the live version; everything else is S1.
 *  Read once at module scope: neither route navigates to the other in-place. */
const isStory = location.pathname.startsWith('/ship/');

export function App() {
  if (isStory) return <StoryPage />;
  return (
    <AppShell>
      <MapCanvas />
    </AppShell>
  );
}
