import type { ReactNode } from "react";

/** The labelled frame of a board section. A section with no content yet renders only the label. */
export function SectionShell({
  name,
  label,
  children,
}: {
  name: string;
  label: string;
  children?: ReactNode;
}) {
  return (
    <section className="sec" data-section={name} aria-label={label}>
      {children}
    </section>
  );
}

/** A section while the data is on its way: empty, no spinner (spec 0006 section 11.6). */
export function SectionPlaceholder({ name, label }: { name: string; label: string }) {
  return (
    <section className="sec sec-loading" data-section={name} aria-label={label} aria-busy="true" />
  );
}
