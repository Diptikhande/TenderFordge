import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiService, type Tender } from '../services/apiService';
import { EmptyState } from './ui/EmptyState';
import { PageHeader } from './ui/PageHeader';
import { SectionCard } from './ui/SectionCard';
import { StatusBadge } from './ui/StatusBadge';

export function TendersPage() {
  const [tenders, setTenders] = useState<Tender[]>([]); const [query, setQuery] = useState(''); const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null);
  useEffect(() => { apiService.getTenders().then(setTenders).catch(() => setError('Tender records could not be loaded. Check that the API is running and try again.')).finally(() => setLoading(false)); }, []);
  const visibleTenders = useMemo(() => tenders.filter((item) => `${item.tender_id} ${item.filename}`.toLowerCase().includes(query.toLowerCase())), [tenders, query]);
  return <main className="page"><div className="page-content"><PageHeader eyebrow="Tender management" title="Tender opportunities" description="Documents selected by your company for capability-based evaluation." actions={<Link to="/upload" className="primary-button">Upload tender</Link>} />
    <SectionCard title="All tenders" description={`${tenders.length} document${tenders.length === 1 ? '' : 's'} available in the connected workspace.`} action={<input aria-label="Search tenders" value={query} onChange={(event) => setQuery(event.target.value)} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-400 sm:w-60" placeholder="Search tender documents" />}>
      {loading ? <div className="empty-state"><p>Loading tender records…</p></div> : error ? <EmptyState title="Unable to load tenders" description={error} /> : visibleTenders.length === 0 ? <EmptyState title={query ? 'No matching tenders' : 'No tender documents yet'} description={query ? 'Try a different tender ID or document name.' : 'Upload a tender document to begin an evaluation.'} action={!query ? <Link className="primary-button" to="/upload">Upload tender</Link> : undefined} /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Tender name</th><th>Document</th><th>Processing status</th><th>Recommendation</th><th aria-label="Actions" /></tr></thead><tbody>{visibleTenders.map((tender) => <tr key={tender.tender_id}><td className="emphasis">{tender.tender_id}</td><td>{tender.filename}</td><td><StatusBadge label={tender.status} tone="warning" /></td><td><StatusBadge label="Not available" tone="neutral" /></td><td><Link className="table-action" to={`/tenders/${tender.tender_id}`}>Open</Link></td></tr>)}</tbody></table></div>}</SectionCard>
  </div></main>;
}
