// Деньги приходят строками с БД ('9055.55') и уходят на экран строками.
// Никаких parseFloat/Number (ADR-003) — только строковые операции.
export function formatMoney(value: string | null | undefined): string {
  if (!value) return '—'
  const negative = value.startsWith('-')
  const body = negative ? value.slice(1) : value
  const [integer, fraction] = body.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '\u202F')
  const withFraction = fraction ? `${grouped},${fraction}` : grouped
  return negative ? `−${withFraction}` : withFraction
}
