import { localTimeZone } from '../lib/time'

export function TimezoneNote() {
  return (
    <p className="text-xs text-[var(--text-muted)]">
      Times are shown in your local time zone (<span className="mono">{localTimeZone()}</span>); each time carries its
      zone abbreviation. Weather target dates are New York calendar dates.
    </p>
  )
}
