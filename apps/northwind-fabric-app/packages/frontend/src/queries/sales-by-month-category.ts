import type { VisualizationSpec } from '@microsoft/fabric-visuals';

import type { ColumnMetadataMap } from '@/lib/to-data-table';

import query from './sales-by-month-category.dax?raw';
import spec from './sales-by-month-category.json';

const connection = 'northwind-model';

const columnMetadata: ColumnMetadataMap = {
  'dim_date[year_month]': {
    name: 'dim_dateyear_month',
    displayName: 'Month',
  },
  'dim_product[category_name]': {
    name: 'dim_productcategory_name',
    displayName: 'Product category',
  },
  '[Sales]': {
    name: 'Sales',
    displayName: 'Sales',
    format: '#,##0.00',
  },
  '[Orders]': {
    name: 'Orders',
    displayName: 'Orders',
    format: '#,##0',
  },
};

export function salesByMonthCategory() {
  return {
    connection,
    query,
    columnMetadata,
    vegaLiteSpec: spec as VisualizationSpec,
  };
}
