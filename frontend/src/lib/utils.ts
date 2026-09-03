/** Склейка классов (аналог cn из эталона): фильтрует falsy и объединяет. */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}
