import type { Meta, StoryObj } from '@storybook/react-vite';
import { FilterChips } from './FilterChips';
import { TopBar } from './TopBar';

/** S1 chrome in both themes — docs/design "Batch 1 Map Shell" :: "First visit"
 *  and "First visit · Blue Marble day". */
function Shell() {
  return (
    <div className="min-h-[260px] bg-[var(--page)]">
      <TopBar />
      <div className="relative h-[200px] bg-[var(--sea-deep)]">
        <div className="absolute top-[18px] left-[20px]">
          <FilterChips />
        </div>
      </div>
    </div>
  );
}

const meta = {
  title: 'Shell/TopBar',
  component: Shell,
} satisfies Meta<typeof Shell>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Night: Story = { parameters: { theme: 'night' } };
export const Day: Story = { parameters: { theme: 'day' } };
