import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]',
  )).filter((element) => {
    if (element.closest('[hidden], [inert]')) return false;
    const closed = element.closest('details:not([open])');
    return !closed || closed.querySelector(':scope > summary') === element;
  });
}

export function useModal<T extends HTMLElement>(open: boolean, onClose: () => void, busy = false) {
  const ref = useRef<T>(null);
  const close = useRef(onClose);
  const locked = useRef(busy);
  close.current = onClose;
  locked.current = busy;
  useEffect(() => {
    const container = ref.current;
    if (!open || !container) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    (focusableElements(container)[0] ?? container).focus({ preventScroll: true });
    const keyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && !locked.current) {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      }
      if (event.key !== 'Tab') return;
      const items = focusableElements(container);
      const first = items[0] ?? container;
      const last = items.at(-1) ?? container;
      if (event.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', keyDown);
    return () => {
      document.removeEventListener('keydown', keyDown);
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [open]);
  return ref;
}

export function tabsKeyDown(event: KeyboardEvent<HTMLElement>) {
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
  const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
    : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
    : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
  if (next < 0) return;
  event.preventDefault();
  tabs[next]?.focus();
  tabs[next]?.click();
}

export function ActionMenu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return <div className="action-menu" ref={ref} onKeyDown={(event) => {
    if (event.key === 'Escape') {
      setOpen(false); ref.current?.querySelector('button')?.focus();
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('.action-menu-content a, .action-menu-content button') ?? []);
      const current = items.indexOf(document.activeElement as HTMLElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : event.key === 'ArrowDown' ? (current + 1) % items.length : (current + items.length - 1) % items.length;
      items[next]?.focus();
    }
  }}>
    <button type="button" className="button" aria-expanded={open} onClick={() => setOpen(!open)}>{label}</button>
    {open && <div className="action-menu-content" onClick={() => setOpen(false)}>{children}</div>}
  </div>;
}

export function AsyncState({ loading, error, retry, children }: {
  loading?: boolean; error?: string; retry?: () => void; children?: ReactNode;
}) {
  if (error) return <div className="alert" role="alert">{error}{retry && <button type="button" className="button" onClick={retry}>↻</button>}</div>;
  if (loading) return <div className="loading" role="status">{children}</div>;
  return <>{children}</>;
}
