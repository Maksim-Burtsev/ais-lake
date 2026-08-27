import type { Decorator, Preview } from '@storybook/react-vite';
import '../src/styles/app.css';

/** Stories declare their theme with `parameters: { theme: 'day' }`; the
 *  decorator stamps it on <html> exactly like the app does. */
const withTheme: Decorator = (Story, context) => {
  const theme = (context.parameters as { theme?: 'night' | 'day' }).theme ?? 'night';
  document.documentElement.setAttribute('data-theme', theme);
  return <Story />;
};

const preview: Preview = {
  decorators: [withTheme],
  parameters: { layout: 'fullscreen' },
};

export default preview;
