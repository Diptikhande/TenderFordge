import { PageHeader } from './ui/PageHeader';
import { StatusBadge } from './ui/StatusBadge';

const sections = [
  ['Basic information', 'Legal name, registration, address, and primary contacts.'], ['Company services', 'Core services, products, and delivery capabilities.'], ['Technical capabilities', 'Tools, technologies, infrastructure, and capacity.'], ['Certifications', 'Standards, certificates, licenses, and compliance evidence.'], ['Experience', 'Years in operation and relevant industry expertise.'], ['Previous projects', 'Completed work that supports tender qualification.'], ['Technical expertise', 'Team composition, roles, and specialist knowledge.'], ['Financial information', 'Financial capacity and qualification evidence.'],
];

export function CompanyProfilePage() { return <main className="page"><div className="page-content"><PageHeader eyebrow="Company capability profile" title="Company Profile" description="The permanent evidence base that will be compared with tender requirements." /><div className="notice"><span className="notice-icon">i</span><div><strong>Profile configuration is not connected yet</strong>These sections establish the enterprise profile structure. No company information is stored until the relevant API is available.</div></div><section className="profile-grid">{sections.map(([title, description]) => <article className="profile-item" key={title}><div className="profile-item-top"><div><h2>{title}</h2><p>{description}</p></div><StatusBadge label="Not configured" tone="neutral" /></div></article>)}</section></div></main>; }
