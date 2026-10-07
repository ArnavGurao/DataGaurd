import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Plus, Search } from 'lucide-react';
import { Button, Empty, JobsTable, PageHeading, SearchInput } from '../components/UI.jsx';
import { ACTIVE, navigate, number, useWorkspace } from '../context.jsx';

export function History({ reports = false }) {
  const { jobs, search, setSearch } = useWorkspace();
  const [filter, setFilter] = useState('all'),
    [page, setPage] = useState(0),
    [sort, setSort] = useState('newest');
  useEffect(() => setPage(0), [filter, search, reports, sort]);
  const candidates = reports ? jobs.filter((j) => j.status === 'COMPLETED') : jobs;
  const filtered = candidates
    .filter(
      (j) =>
        `${j.title} ${j.original_name || ''}`.toLowerCase().includes(search.toLowerCase()) &&
        (filter === 'all' ||
          (filter === 'active' ? ACTIVE.includes(j.status) : j.status === filter)),
    )
    .sort((a, b) =>
      sort === 'newest'
        ? new Date(b.created_at) - new Date(a.created_at)
        : (a.summary?.valid_percentage ?? -1) - (b.summary?.valid_percentage ?? -1),
    );
  const pages = Math.max(1, Math.ceil(filtered.length / 6)),
    current = Math.min(page, pages - 1);
  return (
    <>
      <PageHeading
        eyebrow={reports ? 'THE FULL PICTURE' : 'YOUR DATA LIBRARY'}
        title={reports ? 'Insights you can act on' : 'Every dataset, accounted for'}
        description={
          reports
            ? 'Explore completed audits, understand issues, and take your results with you.'
            : 'Follow every upload from its first check to its final report.'
        }
        action={
          <Button onClick={() => navigate('/upload')}>
            <Plus size={17} />
            Upload dataset
          </Button>
        }
      />
      <section className="panel history-panel">
        <div className="history-toolbar">
          <SearchInput value={search} onChange={setSearch} />
          <div className="filter-controls">
            {!reports && (
              <select
                aria-label="Filter by status"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="all">All statuses</option>
                <option value="COMPLETED">Completed</option>
                <option value="active">In progress</option>
                <option value="FAILED">Failed</option>
              </select>
            )}
            <select
              aria-label="Sort datasets"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="newest">Newest first</option>
              <option value="quality">Lowest quality first</option>
            </select>
          </div>
        </div>
        <div className="table-context">
          <span>
            {number(filtered.length)} {reports ? 'reports' : 'datasets'}
          </span>
          <span>All your validation activity, in one place</span>
        </div>
        {filtered.length ? (
          <JobsTable jobs={filtered.slice(current * 6, current * 6 + 6)} reportMode={reports} />
        ) : (
          <Empty
            title={
              search || filter !== 'all'
                ? 'No datasets match these filters'
                : reports
                  ? 'Your reports will live here'
                  : 'A clean slate'
            }
            description={
              search || filter !== 'all'
                ? 'Try another search or select a different status.'
                : 'Upload your first CSV to start building a clearer picture.'
            }
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  if (search || filter !== 'all') {
                    setSearch('');
                    setFilter('all');
                  } else navigate('/upload');
                }}
              >
                {search || filter !== 'all' ? 'Clear filters' : 'Upload dataset'}
              </Button>
            }
          />
        )}
        <div className="pagination">
          <span>
            {filtered.length
              ? `${current * 6 + 1}–${Math.min((current + 1) * 6, filtered.length)} of ${number(filtered.length)}`
              : '0 results'}
          </span>
          <div>
            <Button
              variant="secondary"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
              aria-label="Previous page"
            >
              <ArrowLeft size={15} />
            </Button>
            <span>
              Page {current + 1} of {pages}
            </span>
            <Button
              variant="secondary"
              disabled={current + 1 >= pages}
              onClick={() => setPage(current + 1)}
              aria-label="Next page"
            >
              <ArrowRight size={15} />
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
