import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState, type ReactNode } from "react";

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
      if (previous?.isConnected && (container.contains(document.activeElement) || document.activeElement === document.body)) previous.focus({ preventScroll: true });
    };
  }, [open]);
  return ref;
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

  }}>
    <button type="button" className="button" aria-expanded={open} onClick={() => setOpen(!open)}>{label}</button>
    {open && <div className="action-menu-content" onClick={() => { ref.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true }); setOpen(false); }}>{children}</div>}
  </div>;
}

export function AsyncState({ loading, error, retry, children }: {
  loading?: boolean; error?: string; retry?: () => void; children?: ReactNode;
}) {
  const { t } = useTranslation();
  if (error) return <div className="alert" role="alert">{error}{retry && <button type="button" className="button" onClick={retry}>{t("retryLoad")}</button>}</div>;
  if (loading) return <div className="loading" role="status">{children}</div>;
  return <>{children}</>;
}
