import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'TenderForge — AI-Powered Tender Intelligence',
  description: 'A multi-agent cognitive RAG platform for tender intelligence.',
}

export const viewport: Viewport = { colorScheme: 'light', themeColor: '#f7fafc' }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" className="bg-background"><body className="antialiased">{children}{process.env.NODE_ENV === 'production' && <Analytics />}</body></html>
}
