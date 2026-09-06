// Деньги приходят строками с БД ('9055.5500') и уходят на экран строками.
// Никаких parseFloat/Number (ADR-003) — только строковые операции.

function groupInteger(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, '\u202F')
}

/** Полный показ как в БД: '9055.5500' → «9 055,5500». */
export function formatMoney(value: string | null | undefined): string {
  if (!value) return '—'
  const negative = value.startsWith('-')
  const body = negative ? value.slice(1) : value
  const [integer, fraction] = body.split('.')
  const withFraction = fraction ? `${groupInteger(integer)},${fraction}` : groupInteger(integer)
  return negative ? `−${withFraction}` : withFraction
}

/** Отображение 2 знака (спека этапа D): '9055.5500' → «9 055,55 ₽». */
export function formatMoney2(value: string | null | undefined, currency?: string): string {
  if (!value) return '—'
  const negative = value.startsWith('-')
  const body = negative ? value.slice(1) : value
  const [integer, fraction = ''] = body.split('.')
  const fraction2 = (fraction + '00').slice(0, 2)
  const withFraction = `${groupInteger(integer)},${fraction2}`
  const sign = negative ? '−' : ''
  return currency ? `${sign}${withFraction}\u202F${moneySymbol(currency)}` : `${sign}${withFraction}`
}

const SYMBOLS: Record<string, string> = { RUB: '₽', USD: '$', EUR: '€', CNY: '¥' }

export function moneySymbol(currency: string): string {
  return SYMBOLS[currency] ?? currency
}

/** Курс: до 4 знаков без хвостовых нулей — '97.5000' → «97,5». */
export function formatRate(value: string | null | undefined): string {
  if (!value) return '—'
  const trimmed = value.replace(/0+$/, '').replace(/\.$/, '')
  return trimmed.replace('.', ',')
}

/** Валидация ввода суммы в диалогах: непустая строка Decimal > 0. */
export function isPositiveDecimalString(value: string): boolean {
  const trimmed = value.trim().replace(',', '.')
  return /^\d+(\.\d+)?$/.test(trimmed) && Number(trimmed) > 0
}
