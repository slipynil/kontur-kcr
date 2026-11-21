import { render, screen } from '@testing-library/react';
import { StructuredDetails } from '../components/structured-details';

it('shows inherited field sources as readable data and preserves false and zero', () => {
  const view = render(<StructuredDetails values={{ status: { quarter: '2025_Q4', snapshot_date: '2025-10-10', batch_id: 'batch-1' }, example: false, unsubmitted_documents: 0 }} />);
  expect(screen.getByText('Статус')).toBeInTheDocument();
  expect(screen.getByText('Исходный квартал')).toBeInTheDocument();
  expect(screen.getByText('2025_Q4')).toBeInTheDocument();
  expect(screen.getByText('Нет')).toBeInTheDocument();
  expect(screen.getByText('0')).toBeInTheDocument();
  expect(view.container.querySelector('pre')).toBeNull();
});
