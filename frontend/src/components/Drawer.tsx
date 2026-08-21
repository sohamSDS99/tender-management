import { useEffect, useRef, type ReactNode } from 'react';

/**
 * Shared drawer shell for the settings and detail panels.
 *
 * Handles the parts that are easy to get wrong and that a keyboard user notices
 * immediately: Escape closes, focus moves into the panel on open and back to
 * whatever opened it on close, Tab cycles inside the panel while it is modal,
 * and the page behind it does not scroll.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  label: string;
  className?: string;
  children: ReactNode;
  /** Extra keys handled while the drawer is open, e.g. j/k navigation. */
  onKeyDown?: (event: KeyboardEvent) => void;
}

export function Drawer({ open, onClose, label, className, children, onKeyDown }: DrawerProps) {
  const panel = useRef<HTMLElement | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;
    const node = panel.current;
    const first = node?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? node)?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key === 'Tab' && node) {
        const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
          (el) => el.offsetParent !== null,
        );
        if (items.length === 0) return;
        const firstItem = items[0];
        const lastItem = items[items.length - 1];
        if (!event.shiftKey && document.activeElement === lastItem) {
          event.preventDefault();
          firstItem.focus();
        } else if (event.shiftKey && document.activeElement === firstItem) {
          event.preventDefault();
          lastItem.focus();
        }
        return;
      }
      onKeyDown?.(event);
    };

    document.addEventListener('keydown', handle);
    return () => {
      document.removeEventListener('keydown', handle);
      document.body.style.overflow = previousOverflow;
      restoreTo.current?.focus?.();
    };
  }, [open, onClose, onKeyDown]);

  return (
    <aside
      ref={panel}
      className={`drawer${className ? ` ${className}` : ''}${open ? ' is-on' : ''}`}
      role="dialog"
      aria-modal={open}
      aria-label={label}
      aria-hidden={!open}
      tabIndex={-1}
      // Fully removed from the tab order while closed: a translated-off panel is
      // still focusable, which strands keyboard users in an invisible form.
      style={open ? undefined : { visibility: 'hidden' }}
    >
      {children}
    </aside>
  );
}
