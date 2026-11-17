import { describe, expect, it } from 'vitest';

import { salesOverview } from './sales-overview';

describe('salesOverview', () => {
  it('uses the unfiltered query when no category is selected', () => {
    const result = salesOverview();

    expect(result.query).toContain('"Total Sales"');
    expect(result.query).not.toContain('__CATEGORY_LITERAL__');
  });

  it('applies and escapes the selected category in the DAX filter', () => {
    expect(salesOverview('Beverages').query).toContain(
      'TREATAS({"Beverages"},'
    );
    expect(salesOverview('Name "with quotes"').query).toContain(
      'TREATAS({"Name ""with quotes"""},'
    );
  });
});
