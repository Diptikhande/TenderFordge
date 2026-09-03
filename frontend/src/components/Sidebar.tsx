import { NavLink } from 'react-router-dom';
import { CogIcon, DocumentChartBarIcon, DocumentIcon, HomeIcon, SparklesIcon, UserIcon } from './icons';

const navItems = [
  { name: 'Dashboard', path: '/', icon: HomeIcon, exact: true },
  { name: 'Company Profile', path: '/company-profile', icon: UserIcon },
  { name: 'Tenders', path: '/tenders', icon: DocumentIcon },
  { name: 'Analysis', path: '/analysis', icon: SparklesIcon },
  { name: 'Reports', path: '/reports', icon: DocumentChartBarIcon },
  { name: 'Settings', path: '/settings', icon: CogIcon },
];

export function Sidebar() {
  return <aside className="sticky top-0 flex h-screen w-[72px] shrink-0 flex-col border-r border-slate-800 bg-slate-950 text-white md:w-64">
    <div className="border-b border-slate-800 px-4 py-5 md:px-6"><div className="flex items-center gap-3"><div className="brand-mark shrink-0">T</div><div className="hidden min-w-0 md:block"><h1 className="truncate text-[17px] font-bold">TenderForge</h1><p className="mt-0.5 text-xs text-slate-400">Tender intelligence</p></div></div></div>
    <nav className="flex-1 space-y-1 px-3 py-5 md:px-4">{navItems.map((item) => { const Icon = item.icon; return <NavLink key={item.path} to={item.path} end={item.exact} title={item.name} className={({ isActive }) => `flex items-center justify-center gap-3 rounded-lg px-3 py-3 text-sm transition-colors md:justify-start ${isActive ? 'bg-slate-800 font-semibold text-white' : 'text-slate-400 hover:bg-slate-900 hover:text-slate-100'}`}><Icon className="h-5 w-5 shrink-0" /><span className="hidden md:block">{item.name}</span></NavLink>; })}
      <NavLink to="/upload" title="Upload Tender" className={({ isActive }) => `mt-4 flex items-center justify-center gap-3 rounded-lg border px-3 py-3 text-sm font-semibold transition-colors md:justify-start ${isActive ? 'border-blue-400 bg-blue-600 text-white' : 'border-slate-700 text-slate-300 hover:border-slate-600 hover:bg-slate-900'}`}><span className="text-base leading-none">+</span><span className="hidden md:block">Upload Tender</span></NavLink>
    </nav>
    <div className="border-t border-slate-800 px-3 py-4 text-center text-[11px] text-slate-500 md:px-4 md:text-left"> <span className="md:hidden">v0.1</span><span className="hidden md:block">Section 1 foundation</span></div>
  </aside>;
}
