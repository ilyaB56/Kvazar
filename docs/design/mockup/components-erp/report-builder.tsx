'use client'

import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { buildReportRows, reportDatasets, reportPeriods, statusLabels, fmtMoney, type ReportDatasetId } from '@/lib/erp-data'
import {
  Banknote,
  Download,
  FileBarChart2,
  Save,
  ShoppingCart,
  UsersRound,
  Warehouse,
  X,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

const datasetIcons: Record<ReportDatasetId, React.ReactNode> = {
  sales: <ShoppingCart className="h-5 w-5" />,
  stock: <Warehouse className="h-5 w-5" />,
  finance: <Banknote className="h-5 w-5" />,
  hr: <UsersRound className="h-5 w-5" />,
}

const datasetTones: Record<ReportDatasetId, string> = {
  sales: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400',
  stock: 'bg-teal-50 text-teal-600 dark:bg-teal-950/60 dark:text-teal-400',
  finance: 'bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400',
  hr: 'bg-orange-50 text-orange-600 dark:bg-orange-950/60 dark:text-orange-400',
}

export function ReportBuilder({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { toast } = useToast()
  const [datasetId, setDatasetId] = useState<ReportDatasetId>('sales')
  const [selected, setSelected] = useState<string[]>(['id', 'customer', 'amount'])
  const [period, setPeriod] = useState(reportPeriods[0])
  const [built, setBuilt] = useState(false)

  const dataset = reportDatasets.find((d) => d.id === datasetId)!
  const rows = built ? buildReportRows(datasetId) : []

  const pickDataset = (id: ReportDatasetId) => {
    setDatasetId(id)
    // разумные поля по умолчанию: первые 3
    const def = reportDatasets.find((d) => d.id === id)!.fields
    setSelected(def.slice(0, 3).map((f) => f.key))
    setBuilt(false)
  }

  const toggleField = (key: string) => {
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
    setBuilt(false)
  }

  const activeFields = dataset.fields.filter((f) => selected.includes(f.key))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl erp-scroll">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileBarChart2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            Конструктор отчёта
          </DialogTitle>
          <DialogDescription>
            Выберите источник, поля и период — макет покажет предпросмотр таблицы
          </DialogDescription>
        </DialogHeader>

        {/* Шаг 1: источник */}
        <section aria-label="Шаг 1 — источник данных">
          <p className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-[11px] font-bold text-white">1</span>
            Источник данных
          </p>
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            {reportDatasets.map((d) => {
              const active = d.id === datasetId
              return (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => pickDataset(d.id)}
                  aria-pressed={active}
                  className={cn(
                    'rounded-xl border p-3 text-left transition-all',
                    active
                      ? 'border-emerald-500 bg-emerald-50/60 ring-2 ring-emerald-500/20 dark:border-emerald-500 dark:bg-emerald-950/40'
                      : 'border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:border-zinc-700 dark:hover:bg-zinc-900'
                  )}
                >
                  <span className={cn('flex h-9 w-9 items-center justify-center rounded-lg', datasetTones[d.id])}>
                    {datasetIcons[d.id]}
                  </span>
                  <p className="mt-2 text-sm font-semibold">{d.name}</p>
                  <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{d.desc}</p>
                </button>
              )
            })}
          </div>
        </section>

        {/* Шаг 2: поля */}
        <section aria-label="Шаг 2 — поля отчёта">
          <p className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-[11px] font-bold text-white">2</span>
            Поля отчёта
            <span className="ml-auto font-normal normal-case tracking-normal text-muted-foreground">
              выбрано {selected.length} из {dataset.fields.length}
            </span>
          </p>
          <div className="flex flex-wrap gap-2">
            {dataset.fields.map((f) => {
              const checked = selected.includes(f.key)
              return (
                <label
                  key={f.key}
                  className={cn(
                    'flex cursor-pointer items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition-all',
                    checked
                      ? 'border-emerald-500 bg-emerald-50 font-medium text-emerald-800 dark:border-emerald-500 dark:bg-emerald-950/40 dark:text-emerald-300'
                      : 'border-zinc-200 text-muted-foreground hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:border-zinc-700 dark:hover:bg-zinc-900'
                  )}
                >
                  <Checkbox
                    checked={checked}
                    onCheckedChange={() => toggleField(f.key)}
                    className="data-[state=checked]:border-emerald-600 data-[state=checked]:bg-emerald-600 data-[state=checked]:text-white"
                    aria-label={`Поле ${f.label}`}
                  />
                  {f.label}
                </label>
              )
            })}
          </div>
        </section>

        {/* Шаг 3: параметры */}
        <section aria-label="Шаг 3 — период и группировка" className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-[11px] font-bold text-white">3</span>
              Период
            </p>
            <Select value={period} onValueChange={(v) => { setPeriod(v); setBuilt(false) }}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {reportPeriods.map((p) => (
                  <SelectItem key={p} value={p}>{p}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground opacity-0 select-none">
              Группировка
            </p>
            <Select defaultValue="none">
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Без группировки</SelectItem>
                {dataset.fields.slice(1, 3).map((f) => (
                  <SelectItem key={f.key} value={f.key}>По полю «{f.label}»</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </section>

        {/* Предпросмотр */}
        <AnimatePresence initial={false}>
          {built && (
            <motion.section
              key="preview"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              aria-label="Предпросмотр отчёта"
              className="overflow-hidden rounded-xl border dark:border-zinc-800"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-zinc-50/80 px-4 py-2.5 dark:bg-zinc-900/50">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Предпросмотр · {dataset.name} · {period}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  строк: {rows.length} · сформирован 18.12.2025
                </p>
              </div>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-zinc-50/50 hover:bg-zinc-50/50 dark:bg-transparent">
                      {activeFields.map((f) => (
                        <TableHead key={f.key}>{f.label}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((row, i) => (
                      <TableRow key={i}>
                        {activeFields.map((f) => (
                          <TableCell
                            key={f.key}
                            className={cn(
                              'text-sm',
                              f.money && 'text-right font-medium tabular-nums whitespace-nowrap',
                              f.key === 'status' && 'text-muted-foreground'
                            )}
                          >
                            {f.money
                              ? fmtMoney(Number(row[f.key]))
                              : statusLabels[String(row[f.key])] ?? String(row[f.key])}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </motion.section>
          )}
        </AnimatePresence>

        <DialogFooter className="gap-2 sm:gap-0">
          <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-between">
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
                disabled={selected.length === 0}
                onClick={() => setBuilt(true)}
              >
                <FileBarChart2 className="h-3.5 w-3.5" /> Сформировать
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={!built}
                onClick={() => toast({ title: 'Файл сформирован', description: `${dataset.name} · ${period} · XLSX, 1 лист`, duration: 3000 })}
              >
                <Download className="h-3.5 w-3.5" /> XLSX
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5"
                disabled={!built}
                onClick={() => toast({ title: 'Шаблон сохранён', description: `«${dataset.name} · ${period}» появится в библиотеке отчётов`, duration: 3000 })}
              >
                <Save className="h-3.5 w-3.5" /> В шаблоны
              </Button>
            </div>
            <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => onOpenChange(false)}>
              <X className="h-3.5 w-3.5" /> Закрыть
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
