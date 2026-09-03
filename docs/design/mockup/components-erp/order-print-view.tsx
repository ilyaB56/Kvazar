'use client'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { getOrderItems, fmtMoney, DEMO_TODAY, type OrderRow } from '@/lib/erp-data'
import { Printer, FileDown, X, Building2, User, BadgeCheck } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'

const VAT_RATE = 0.2

const fmtDate = (d: Date) =>
  d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })

// Детерминированный номер накладной из номера заказа: ЗК-10234 → 1023401-Т
const docNumber = (orderId: string) => orderId.replace(/\D/g, '') || '0000'

/**
 * Печатная форма заказа — стилизована под бумажный документ (Торг-12).
 * При печати через @media print на бумагу попадает только блок [data-erp-print].
 */
export function OrderPrintView({
  order,
  open,
  onOpenChange,
}: {
  order: OrderRow | null
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const { toast } = useToast()
  const items = order ? getOrderItems(order) : []
  const delivery = order && order.status !== 'draft' ? 24_500 : 0
  const goodsSum = order ? order.amount - delivery : 0
  const vat = Math.round((goodsSum / (1 + VAT_RATE)) * VAT_RATE)

  const doPrint = () => {
    toast({
      title: 'Документ отправлен на печать',
      description: `${order?.id} · Торг-12 · 1 копия`,
      duration: 3000,
    })
    // В реальной системе здесь window.print(); в макете — имитация,
    // чтобы не открывать системный диалог печати в песочнице
    if (typeof window !== 'undefined') {
      // window.print()
    }
  }

  const docNum = order ? docNumber(order.id) : '0000'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl erp-scroll">
        {order && (
          <>
            <DialogHeader>
              <DialogTitle className="text-lg">Печатная форма · Торг-12</DialogTitle>
              <DialogDescription>
                Товарная накладная для заказа {order.id} · предпросмотр перед печатью
              </DialogDescription>
            </DialogHeader>

            {/* «Бумага» */}
            <div
              data-erp-print
              className="mx-auto w-full max-w-[720px] rounded-sm border border-zinc-300 bg-white px-8 py-7 text-[13px] leading-relaxed text-zinc-900 shadow-sm dark:border-zinc-600"
            >
              {/* Шапка документа */}
              <div className="flex items-start justify-between gap-4 border-b-2 border-zinc-900 pb-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-md bg-emerald-600 text-base font-bold text-white">
                    Т
                  </span>
                  <div>
                    <p className="text-sm font-bold uppercase tracking-wide">ООО «ТехноПром»</p>
                    <p className="text-[10px] leading-snug text-zinc-600">
                      123112, г. Москва, Пресненская наб., д. 12, БЦ «Башня»
                      <br />
                      ИНН 7701234567 · КПП 770101001 · ОГРН 1157746001234 · р/с 40702810400000012345, АО «Альфа-Банк»
                    </p>
                  </div>
                </div>
                <div className="shrink-0 text-right text-[10px] text-zinc-600">
                  <p>Телефон: +7 (495) 123-45-67</p>
                  <p>sales@technoprom.ru</p>
                  <p className="mt-1 inline-flex items-center gap-1 rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 font-semibold text-emerald-700">
                    <BadgeCheck className="h-3 w-3" /> ЭДО · подписано КЭП
                  </p>
                </div>
              </div>

              {/* Название документа */}
              <div className="py-4 text-center">
                <p className="text-base font-bold uppercase tracking-widest">
                  Товарная накладная № {docNum}/Т
                </p>
                <p className="text-[11px] text-zinc-600">
                  от {fmtDate(DEMO_TODAY)} · основание: заказ {order.id} от {order.date} · унифицированная форма Торг-12
                </p>
              </div>

              {/* Поставщик / Покупатель */}
              <div className="mb-4 grid grid-cols-2 gap-3">
                <div className="rounded border border-zinc-400 p-2.5">
                  <p className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-zinc-600">
                    <Building2 className="h-3 w-3" /> Поставщик
                  </p>
                  <p className="text-[12px] font-semibold">ООО «ТехноПром»</p>
                  <p className="text-[11px] text-zinc-600">ИНН 7701234567 · Москва</p>
                </div>
                <div className="rounded border border-zinc-400 p-2.5">
                  <p className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-zinc-600">
                    <User className="h-3 w-3" /> Покупатель
                  </p>
                  <p className="text-[12px] font-semibold">{order.customer}</p>
                  <p className="text-[11px] text-zinc-600">отгрузка: {order.channel}</p>
                </div>
              </div>

              {/* Таблица позиций */}
              <Table className="border">
                <TableHeader>
                  <TableRow className="!border-zinc-400 !bg-zinc-100 hover:!bg-zinc-100">
                    <TableHead className="w-8 !border-zinc-400 !text-center !text-[11px] !text-zinc-900">№</TableHead>
                    <TableHead className="!border-zinc-400 !text-[11px] !text-zinc-900">Артикул</TableHead>
                    <TableHead className="!border-zinc-400 !text-[11px] !text-zinc-900">Наименование</TableHead>
                    <TableHead className="w-14 !border-zinc-400 !text-right !text-[11px] !text-zinc-900">Кол-во</TableHead>
                    <TableHead className="w-24 !border-zinc-400 !text-right !text-[11px] !text-zinc-900">Цена, ₽</TableHead>
                    <TableHead className="w-28 !border-zinc-400 !text-right !text-[11px] !text-zinc-900">Сумма, ₽</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((it, i) => (
                    <TableRow key={it.sku}>
                      <TableCell className="border border-zinc-400 text-center text-[12px]">{i + 1}</TableCell>
                      <TableCell className="border border-zinc-400 font-mono text-[11px]">{it.sku}</TableCell>
                      <TableCell className="border border-zinc-400 text-[12px]">{it.name}</TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px]">{it.qty}</TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px]">
                        {it.price.toLocaleString('ru-RU')}
                      </TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px] font-medium">
                        {(it.qty * it.price).toLocaleString('ru-RU')}
                      </TableCell>
                    </TableRow>
                  ))}
                  {/* Доставка */}
                  {delivery > 0 && (
                    <TableRow>
                      <TableCell className="border border-zinc-400 text-center text-[12px]">{items.length + 1}</TableCell>
                      <TableCell className="border border-zinc-400 font-mono text-[11px]">SRV-DEL</TableCell>
                      <TableCell className="border border-zinc-400 text-[12px]">Доставка до склада покупателя</TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px]">1</TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px]">
                        {delivery.toLocaleString('ru-RU')}
                      </TableCell>
                      <TableCell className="border border-zinc-400 text-right text-[12px] font-medium">
                        {delivery.toLocaleString('ru-RU')}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>

              {/* Итоги */}
              <div className="mt-3 ml-auto w-full max-w-[320px] space-y-1 text-[12px]">
                <div className="flex justify-between">
                  <span>Итого без НДС:</span>
                  <span className="font-medium tabular-nums">{fmtMoney(order.amount - vat)}</span>
                </div>
                <div className="flex justify-between">
                  <span>НДС 20%:</span>
                  <span className="font-medium tabular-nums">{fmtMoney(vat)}</span>
                </div>
                <div className="flex justify-between border-t-2 border-zinc-900 pt-1 text-[13px] font-bold">
                  <span>Всего к оплате:</span>
                  <span className="tabular-nums">{fmtMoney(order.amount)}</span>
                </div>
                <p className="pt-0.5 text-right text-[10px] text-zinc-600">
                  Всего наименований: {items.length + (delivery > 0 ? 1 : 0)}
                </p>
              </div>

              {/* Подписи */}
              <div className="mt-6 grid grid-cols-2 gap-8 text-[11px] text-zinc-700">
                <div>
                  <p className="mb-6 font-semibold">Отпустил (менеджер):</p>
                  <div className="flex items-end gap-2">
                    <span className="w-28 border-b border-zinc-500 pb-0.5">{order.manager}</span>
                    <span className="w-10 border-b border-zinc-500" />
                    <span className="text-[10px] text-zinc-500">подпись</span>
                  </div>
                </div>
                <div>
                  <p className="mb-6 font-semibold">Получил (грузополучатель):</p>
                  <div className="flex items-end gap-2">
                    <span className="w-28 border-b border-zinc-500 pb-0.5" />
                    <span className="w-10 border-b border-zinc-500" />
                    <span className="text-[10px] text-zinc-500">подпись</span>
                  </div>
                </div>
              </div>

              <p className="mt-5 border-t border-dashed border-zinc-300 pt-2 text-center text-[9px] text-zinc-500">
                Документ сформирован в макете ERP «ТехноПром» · суммы демонстрационные · МЧД и КЭП — имитация
              </p>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700" onClick={doPrint}>
                    <Printer className="h-3.5 w-3.5" /> Печать
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() =>
                      toast({
                        title: 'PDF сформирован',
                        description: `Торг-12 № ${docNum}/Т сохранён в «Отчёты → Документы»`,
                        duration: 3000,
                      })
                    }
                  >
                    <FileDown className="h-3.5 w-3.5" /> Скачать PDF
                  </Button>
                </div>
                <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => onOpenChange(false)}>
                  <X className="h-3.5 w-3.5" /> Закрыть
                </Button>
              </div>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
