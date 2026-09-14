const paths: Record<string, string> = {
  dashboard: 'M3 10 12 3l9 7 M5 9v12h14V9 M9 21v-7h6v7',
  newRun: 'M12 5v14 M5 12h14',
  runManagement: 'M8 3H5v18h14V3h-3 M8 2h8v4H8z M8 10l2 2 3-3 M8 16h8',
  researchTimelines: 'M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4z M13 7a3 3 0 0 1 3-3h5v15h-4a4 4 0 0 0-4 2',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M10 2h4l1 3 3 1 3 3-1 3 1 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 1-3-1-3 3-3 3-1z',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  history: 'M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2',
  check: 'M5 12l4 4L19 6',
  compare: 'M3 5h7v14H3z M14 5h7v14h-7z',
  close: 'M6 6l12 12 M18 6 6 18',
};
export default function Icon({ name }: { name: string }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] ?? paths.menu} /></svg>;
}
