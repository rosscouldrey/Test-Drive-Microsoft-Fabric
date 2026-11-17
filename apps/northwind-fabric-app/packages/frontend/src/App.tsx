//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { DataGrid } from '@microsoft/fabric-datagrid';
import { VegaVisual, useCssTheme } from '@microsoft/fabric-visuals';
import type { InteractionEvent } from '@microsoft/fabric-visuals-core';
import { filterRows } from '@microsoft/fabric-visuals-core';
import { Moon, RefreshCw, Sun, X } from 'lucide-react';
import { useState } from 'react';

import { useSemanticModelQuery } from '@/hooks/use-semantic-model-query';
import { useTheme } from '@/hooks/theme.context';
import { toDataTable } from '@/lib/to-data-table';
import { salesByMonthCategory, salesOverview } from '@/queries';
import type { SemanticModelQueryResult } from '@microsoft/rayfin-connector-fabric-semanticmodel';

const countFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 0,
});
const salesFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function queryErrorMessage(
  thrown: Error | undefined,
  result: SemanticModelQueryResult | undefined
): string | undefined {
  if (thrown) {
    if (/permission|unauthori[sz]ed|token|sign.?in|401|403/i.test(thrown.message)) {
      return 'Your Microsoft sign-in could not access Northwind Model. Sign in with an account that can view the model, then retry.';
    }
    return 'Northwind Model could not be reached. Check the connection and retry; contact the app owner if this continues.';
  }

  if (result?.status === 'error') {
    if (result.error.category === 'api') {
      return 'Your Microsoft sign-in could not access Northwind Model. Sign in with an account that can view the model, then retry.';
    }
    return `The model query failed (${result.error.category}). Retry, and contact the app owner if it continues.`;
  }

  return undefined;
}

function valueForColumn(
  result: SemanticModelQueryResult | undefined,
  columnName: string
): number | undefined {
  if (result?.status !== 'success') return undefined;

  const columnIndex = result.table.columns.findIndex(
    (column) => column.name === columnName
  );
  const value = columnIndex < 0 ? undefined : result.table.rows[0]?.[columnIndex];
  return typeof value === 'number' ? value : undefined;
}

function categoryFromInteraction(events: InteractionEvent[]): string | null {
  for (const event of events) {
    if (event.action === 'clear') return null;

    for (const selection of event.selections) {
      for (const predicate of selection.predicates) {
        if (
          predicate.name === 'dim_productcategory_name' &&
          predicate.type === 'set' &&
          predicate.values.length === 1 &&
          typeof predicate.values[0] === 'string'
        ) {
          return predicate.values[0];
        }
      }
    }
  }

  return null;
}

function QueryError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="flex flex-col gap-300 rounded-lg border border-destructive/30 bg-destructive/5 p-500 sm:flex-row sm:items-center sm:justify-between"
      role="alert"
    >
      <p className="text-300 leading-300 text-foreground">{message}</p>
      <button
        className="inline-flex h-900 shrink-0 items-center justify-center gap-200 rounded-md border border-border bg-card px-400 text-300 font-medium text-card-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={onRetry}
        type="button"
      >
        <RefreshCw className="icon-size-200" aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

function MetricCard({
  label,
  value,
  isLoading,
  error,
}: {
  label: string;
  value: number | undefined;
  isLoading: boolean;
  error: boolean;
}) {
  return (
    <article className="rounded-lg border border-border border-t-2 border-t-primary bg-card p-500 shadow-sm">
      <h2 className="text-200 leading-200 font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </h2>
      {isLoading ? (
        <div
          aria-label={`Loading ${label.toLowerCase()}`}
          className="mt-400 h-800 w-2/3 animate-pulse rounded-md bg-accent"
        />
      ) : (
        <p className="mt-300 font-numeric text-600 leading-600 font-semibold tabular-nums">
          {error || value === undefined
            ? '—'
            : label === 'Total sales'
              ? salesFormatter.format(value)
              : countFormatter.format(value)}
        </p>
      )}
      <p className="mt-200 text-200 leading-200 text-muted-foreground">
        From the full model
      </p>
    </article>
  );
}

function App() {
  const theme = useCssTheme();
  const { isDark, toggleTheme } = useTheme();
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const summaryQuery = salesOverview(selectedCategory ?? undefined);
  const summary = useSemanticModelQuery(summaryQuery);
  const trendQuery = salesByMonthCategory();
  const trend = useSemanticModelQuery(trendQuery);
  const summaryError = queryErrorMessage(summary.error, summary.data);
  const trendError = queryErrorMessage(trend.error, trend.data);
  const isRefreshing = summary.isLoading || trend.isLoading;
  const trendTable =
    trend.data?.status === 'success'
      ? toDataTable(trend.data.table, trendQuery.columnMetadata)
      : undefined;
  const categoryColumnIndex =
    trendTable?.columns.findIndex(
      (column) => column.name === 'dim_productcategory_name'
    ) ?? -1;
  const visibleTable =
    trendTable && selectedCategory && categoryColumnIndex >= 0
      ? filterRows(
          trendTable,
          (row) => row[categoryColumnIndex] === selectedCategory
        )
      : trendTable;
  const categories =
    trendTable && categoryColumnIndex >= 0
      ? [
          ...new Set(
            trendTable.rows
              .map((row) => row[categoryColumnIndex])
              .filter((value): value is string => typeof value === 'string')
          ),
        ]
      : [];
  const months = trendTable
    ? [...new Set(trendTable.rows.map((row) => row[0]).filter(
        (value): value is string => typeof value === 'string'
      ))]
    : [];
  const monthRange =
    months.length > 0 ? `${months[0]} – ${months[months.length - 1]}` : undefined;
  const trendRows = trendTable?.rows.length ?? 0;

  function refreshAll() {
    void Promise.all([summary.refetch(), trend.refetch()]);
  }

  function handleInteraction(events: InteractionEvent[]) {
    setSelectedCategory(categoryFromInteraction(events));
  }

  return (
    <main className="min-h-full bg-background">
      <div className="mx-auto max-w-7xl px-400 py-600 sm:px-600 lg:px-800 lg:py-800">
        <header className="flex flex-col gap-500 border-b border-border pb-600 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="flex items-center gap-200 text-200 leading-200 font-semibold uppercase tracking-widest text-primary">
              <span className="h-200 w-200 rounded-full bg-primary" aria-hidden="true" />
              Northwind · semantic model
            </p>
            <h1 className="mt-300 font-heading text-hero-800 leading-hero-800 text-foreground">
              Sales, in view.
            </h1>
            <p className="mt-200 max-w-2xl text-300 leading-300 text-muted-foreground">
              A live look at sales performance across product categories.
            </p>
          </div>
          <div className="flex items-center gap-200">
            <button
              aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
              className="inline-flex h-900 w-900 items-center justify-center rounded-md border border-border bg-card text-card-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={toggleTheme}
              type="button"
            >
              {isDark ? (
                <Sun className="icon-size-200" aria-hidden="true" />
              ) : (
                <Moon className="icon-size-200" aria-hidden="true" />
              )}
            </button>
            <button
              aria-label="Refresh sales data"
              className="inline-flex h-900 items-center justify-center gap-200 rounded-md bg-primary px-400 text-300 font-semibold text-primary-foreground transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isRefreshing}
              onClick={refreshAll}
              type="button"
            >
              <RefreshCw
                className={`icon-size-200${isRefreshing ? ' animate-spin' : ''}`}
                aria-hidden="true"
              />
              Refresh
            </button>
          </div>
        </header>

        {summaryError && (
          <div className="mt-500">
            <QueryError message={summaryError} onRetry={() => void summary.refetch()} />
          </div>
        )}

        <div className="mt-500 flex flex-wrap items-end justify-between gap-300">
          <label className="flex flex-col gap-100 text-200 leading-200 font-medium text-muted-foreground">
            Product category
            <select
              aria-label="Filter by product category"
              className="h-900 min-w-44 rounded-md border border-input bg-card px-300 text-300 text-card-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
              disabled={trend.isLoading || categories.length === 0}
              onChange={(event) =>
                setSelectedCategory(event.target.value || null)
              }
              value={selectedCategory ?? ''}
            >
              <option value="">All categories</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>
          <p className="text-200 leading-200 text-muted-foreground">
            {selectedCategory
              ? `Showing ${selectedCategory} across the report.`
              : 'Showing all product categories.'}
          </p>
        </div>

        <section
          aria-label="Sales summary"
          className="mt-400 grid gap-300 sm:grid-cols-2 xl:grid-cols-4"
        >
          <MetricCard
            label="Total sales"
            value={valueForColumn(summary.data, '[Total Sales]')}
            isLoading={summary.isLoading}
            error={Boolean(summaryError)}
          />
          <MetricCard
            label="Orders"
            value={valueForColumn(summary.data, '[Order Count]')}
            isLoading={summary.isLoading}
            error={Boolean(summaryError)}
          />
          <MetricCard
            label="Customers"
            value={valueForColumn(summary.data, '[Customer Count]')}
            isLoading={summary.isLoading}
            error={Boolean(summaryError)}
          />
          <MetricCard
            label="Products"
            value={valueForColumn(summary.data, '[Product Count]')}
            isLoading={summary.isLoading}
            error={Boolean(summaryError)}
          />
        </section>

        <section
          aria-labelledby="trend-title"
          className="mt-700 flex h-chart flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm"
        >
          <div className="flex flex-wrap items-end justify-between gap-300 px-500 pt-500">
            <div>
              <h2
                id="trend-title"
                className="text-400 leading-400 font-semibold text-card-foreground"
              >
                Monthly sales by category
              </h2>
              <p className="mt-100 text-200 leading-200 text-muted-foreground">
                {monthRange
                  ? `${monthRange} · ${visibleTable?.rows.length ?? trendRows} month/category results`
                  : 'Sales amount grouped by month and product category'}
              </p>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center px-300 pb-300 pt-200">
            {trend.isLoading ? (
              <div
                aria-label="Loading monthly sales"
                className="h-full w-full animate-pulse rounded-lg bg-accent"
              />
            ) : trendError ? (
              <QueryError
                message={trendError}
                onRetry={() => void trend.refetch()}
              />
            ) : visibleTable && visibleTable.rows.length > 0 ? (
              <VegaVisual
                spec={trendQuery.vegaLiteSpec}
                data={visibleTable}
                theme={theme}
                onInteraction={handleInteraction}
              />
            ) : (
              <p className="text-300 leading-300 text-muted-foreground">
                No sales data is available in this model.
              </p>
            )}
          </div>
        </section>

        <section
          aria-labelledby="detail-title"
          className="mt-700 overflow-hidden rounded-xl border border-border bg-card shadow-sm"
        >
          <div className="flex flex-wrap items-center justify-between gap-300 px-500 py-400">
            <div>
              <h2
                id="detail-title"
                className="text-400 leading-400 font-semibold text-card-foreground"
              >
                Sales detail
              </h2>
              <p className="mt-100 text-200 leading-200 text-muted-foreground">
                Monthly totals and order counts by product category.
              </p>
            </div>
            {selectedCategory ? (
              <button
                className="inline-flex h-800 items-center gap-200 rounded-md border border-border bg-secondary px-300 text-200 font-medium text-secondary-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setSelectedCategory(null)}
                type="button"
              >
                {selectedCategory}
                <X className="icon-size-100" aria-hidden="true" />
                <span className="sr-only">Clear category filter</span>
              </button>
            ) : (
              <p className="text-200 leading-200 text-muted-foreground">
                Select a category in the chart to filter the rows.
              </p>
            )}
          </div>
          <div className="h-grid overflow-auto border-t border-border">
            {trend.isLoading ? (
              <div
                aria-label="Loading sales detail"
                className="h-full min-h-800 animate-pulse bg-accent"
              />
            ) : trendError ? (
              <div className="p-500">
                <QueryError
                  message={trendError}
                  onRetry={() => void trend.refetch()}
                />
              </div>
            ) : visibleTable && visibleTable.rows.length > 0 ? (
              <DataGrid
                data={visibleTable}
                theme={theme}
                pageSize={16}
                capabilities={{ virtualization: true }}
                onInteraction={handleInteraction}
              />
            ) : (
              <p className="p-600 text-center text-300 leading-300 text-muted-foreground">
                No sales data is available for this view.
              </p>
            )}
          </div>
        </section>

        <footer className="flex flex-wrap items-center justify-between gap-200 py-500 text-200 leading-200 text-muted-foreground">
          <span>Source: Northwind Model</span>
          <span>Sales amounts are shown in the model’s currency.</span>
        </footer>
      </div>
    </main>
  );
}

export default App;
