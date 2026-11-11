// The 6 Phosphor icons of the "right now" section (spec 0006 section 11.9, decision R12), bundled
// from the @phosphor-icons/react package (MIT) so the page requests no icon from another host.
// Each icon is aria-hidden: the word beside it carries the meaning.

import {
  CalendarBlank,
  CheckCircle,
  Clock,
  LockSimple,
  MinusCircle,
  XCircle,
} from "@phosphor-icons/react";

export const ANSWER_ICONS = {
  "check-circle": CheckCircle,
  "x-circle": XCircle,
  clock: Clock,
  "calendar-blank": CalendarBlank,
  "lock-simple": LockSimple,
  "minus-circle": MinusCircle,
} as const;

export type AnswerIconName = keyof typeof ANSWER_ICONS;

export function AnswerIcon({
  name,
  size,
  color,
}: {
  name: AnswerIconName;
  size: number;
  color: string;
}) {
  const Icon = ANSWER_ICONS[name];
  // Only check-circle is filled; the others are regular.
  return (
    <Icon
      aria-hidden="true"
      size={size}
      color={color}
      weight={name === "check-circle" ? "fill" : "regular"}
      data-icon={name}
    />
  );
}
