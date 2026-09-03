import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiService, type Tender } from '../services/apiService';
import { EmptyState } from './ui/EmptyState';
import { PageHeader } from './ui/PageHeader';
import { SectionCard } from './ui/SectionCard';
import { StatCard } from './ui/StatCard';
import { StatusBadge } from './ui/StatusBadge';

export function Dashboard() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { apiService.getTenders().then((data) => setTenders(data)).catch(() => setError('Tender records could not be loaded. Check that the API is running and try again.')).finally(() => setLoading(false)); }, []);
  return <main className="page"><div className="page-content"><PageHeader eyebrow="Tender intelligence" title="Executive overview" description="A focused view of tender documents your company has selected for evaluation." actions={<Link to="/upload" className="primary-button">Upload tender</Link>} />
    <div className="notice"><span className="notice-icon">i</span><div><strong>Analysis is not available yet</strong>Document counts below come from the connected tender API. Eligibility, risk, and recommendation data will appear only when supplied by the platform.</div></div>
    <div className="stat-grid"><StatCard label="Total tenders" value={loading ? '—' : tenders.length} detail="Documents in the current workspace" accent="blue" /><StatCard label="Tenders analyzed" value="—" detail="No analysis data available" accent="slate" /><StatCard label="Recommended to bid" value="—" detail="No recommendation data available" accent="green" /><StatCard label="High risk tenders" value="—" detail="No risk data available" accent="amber" /></div>
    <SectionCard title="Recent tenders" description="Tender documents currently available through the connected API." action={<Link className="table-action" to="/tenders">View all</Link>}>{loading ? <div className="empty-state"><p>Loading tender records…</p></div> : error ? <EmptyState title="Unable to load tenders" description={error} /> : tenders.length === 0 ? <EmptyState title="No tender documents yet" description="Upload a tender PDF to begin building your company’s evaluation workspace." action={<Link className="primary-button" to="/upload">Upload tender</Link>} /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Tender ID</th><th>Document</th><th>Processing status</th><th aria-label="Action" /></tr></thead><tbody>{tenders.slice(0, 5).map((tender) => <tr key={tender.tender_id}><td className="emphasis">{tender.tender_id}</td><td>{tender.filename}</td><td><StatusBadge label={tender.status} tone="warning" /></td><td><Link className="table-action" to={`/tenders/${tender.tender_id}`}>View details</Link></td></tr>)}</tbody></table></div>}</SectionCard>
  </div></main>;
}
