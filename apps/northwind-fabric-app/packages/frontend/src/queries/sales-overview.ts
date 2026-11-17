import categoryQuery from './sales-overview-by-category.dax?raw';
import query from './sales-overview.dax?raw';

const connection = 'northwind-model';

export function salesOverview(category?: string) {
  const filteredQuery =
    category === undefined
      ? query
      : categoryQuery.replace(
          '__CATEGORY_LITERAL__',
          `"${category.replaceAll('"', '""')}"`
        );

  return { connection, query: filteredQuery };
}
