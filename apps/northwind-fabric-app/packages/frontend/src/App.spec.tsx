import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-semantic-model-query', () => ({
  useSemanticModelQuery: () => ({
    data: undefined,
    isLoading: true,
    error: undefined,
    refetch: vi.fn(),
  }),
}));

import App from '@/App';

describe('App', () => {
  it('renders the sales dashboard while model data loads', () => {
    render(<App />);

    expect(
      screen.getByRole('heading', { name: 'Sales, in view.' })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Monthly sales by category' })
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Loading monthly sales')).toBeInTheDocument();
    expect(
      screen.getByRole('combobox', { name: 'Filter by product category' })
    ).toBeDisabled();
  });
});
