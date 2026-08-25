import { useEffect, useRef, type ReactNode } from 'react';
import { Icon } from '../Icon';

/**
 * The frame every full-width settings category renders inside.
 *
 * A settings page takes the width and hides the tender list, which is a bigger
 * move than the old panel made — so it owes the reader two things the panel did
 * not. It says where they are (`Settings / Automation`, not a bare heading), and
 * it gives them one obvious way back that is not the browser button. Escape does
 * the same thing, because a surface that covers the work should always be
 * closeable from the keyboard.
 *
 * Hiding the list is the deliberate trade: nothing on these pages changes what
 * the list contains. Filters, which do, stay on the panel that leaves the
 * results visible.
 */
export function SettingsPage({
  title,
  blurb,
  onBack,
  children,
}: {
  title: string;
  blurb: string;
  onBack: () => void;
  children: ReactNode;
}) {
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  // Move focus to the heading on arrival: without it, focus is still on a menu
  // row that no longer exists, and a screen reader announces nothing at all.
  useEffect(() => {
    headingRef.current?.focus();
  }, [title]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onBack]);

  return (
    <section className="spage" aria-label={`Settings: ${title}`}>
      <header className="spage__head">
        <button type="button" className="btn btn--sm" onClick={onBack}>
          <Icon name="prev" size={13} />
          Back to tenders
        </button>
        <p className="spage__crumb">
          <span>Settings</span>
          <Icon name="chevronRight" size={11} />
          <b>{title}</b>
        </p>
      </header>

      <h2 className="spage__title" tabIndex={-1} ref={headingRef}>
        {title}
      </h2>
      <p className="spage__blurb">{blurb}</p>

      <div className="spage__body">{children}</div>
    </section>
  );
}

/** One titled group of controls inside a settings page. */
export function SettingsSection({
  title,
  note,
  children,
}: {
  title: string;
  /** One line saying what this group decides. Never decoration. */
  note?: string;
  children: ReactNode;
}) {
  return (
    <section className="ssection">
      <div className="ssection__head">
        <h3>{title}</h3>
        {note ? <p>{note}</p> : null}
      </div>
      <div className="ssection__body">{children}</div>
    </section>
  );
}

/** A label / control row inside a section, for single settings. */
export function SettingsRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="srow">
      <div className="srow__label">
        <p>{label}</p>
        {hint ? <small>{hint}</small> : null}
      </div>
      <div className="srow__control">{children}</div>
    </div>
  );
}
