import { useLocation } from 'react-router-dom';

const pageNames: Record<string, string> = { '/': 'Dashboard', '/company-profile': 'Company Profile', '/tenders': 'Tenders', '/upload': 'Upload Tender', '/analysis': 'Analysis', '/reports': 'Reports', '/settings': 'Settings' };

export function Topbar() {
  const { pathname } = useLocation();
  const title = pathname.startsWith('/tenders/') ? 'Tender Details' : pageNames[pathname] ?? 'TenderForge';
  return <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5 md:px-8"><div><p className="text-[11px] font-semibold uppercase tracking-[.08em] text-slate-400">Workspace</p><p className="mt-0.5 text-sm font-semibold text-slate-800">{title}</p></div><div className="flex items-center gap-3"><button type="button" aria-label="Notifications unavailable" className="grid h-9 w-9 place-items-center rounded-full border border-slate-200 text-slate-500 hover:bg-slate-50"><svg width="17" height="17" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6 6 0 10-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 01-6 0v-1m6 0H9" /></svg></button><div className="flex items-center gap-2 border-l border-slate-200 pl-3"><div className="grid h-8 w-8 place-items-center rounded-full bg-slate-100 text-xs font-bold text-slate-600">TF</div><div className="hidden sm:block"><p className="text-xs font-semibold text-slate-700">TenderForge</p><p className="text-[11px] text-slate-400">Workspace</p></div></div></div></header>;
}
