import { EmptyState } from './ui/EmptyState';
import { PageHeader } from './ui/PageHeader';
import { SectionCard } from './ui/SectionCard';

export function ReportsPage() { return <main className="page"><div className="page-content"><PageHeader eyebrow="Tender intelligence" title="Reports" description="Generated intelligence reports will be organized here once completed analysis is available." /><SectionCard title="Tender intelligence reports" description="Reports are created only from completed, evidence-backed analysis."><EmptyState title="No reports available" description="The current platform foundation has no generated reports. Download actions will appear only when report files are available." /></SectionCard></div></main>; }
