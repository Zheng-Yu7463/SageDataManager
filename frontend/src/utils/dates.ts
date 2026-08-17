const ISO_DATE_TIME_WITHOUT_TIMEZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

export function parseApiDate(value: string): Date {
  return new Date(ISO_DATE_TIME_WITHOUT_TIMEZONE.test(value) ? `${value}Z` : value)
}
