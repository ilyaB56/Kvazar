'use client'

import { useState, useSyncExternalStore } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  Share2,
  Link2,
  Copy,
  Check,
  FileText,
  FileArchive,
  Download,
  LogIn,
  TerminalSquare,
  ChevronDown,
  ShieldCheck,
} from 'lucide-react'

const DOWNLOADS = [
  {
    icon: FileText,
    tone: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300',
    title: 'Презентация макетов (PDF)',
    desc: 'Экраны сборки v0.11: все разделы, интеграционное ядро и мобильный вид',
    href: '/downloads/quasar-erp-mockups.pdf',
    fileName: 'quasar-erp-mockups.pdf',
    badge: null,
  },
  {
    icon: FileArchive,
    tone: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
    title: 'Исходный код (ZIP)',
    desc: 'Собирается в момент скачивания — в архиве всегда последняя сборка',
    href: '/api/export/source',
    fileName: 'quasar-erp-source.zip',
    badge: 'всегда актуальная',
  },
] as const

export function ShareDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const { toast } = useToast()
  const [copied, setCopied] = useState(false)

  // hydration-safe: URL известен только на клиенте
  const emptySubscribe = () => () => {}
  const mounted = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  )
  const shareUrl = mounted ? `${window.location.origin}/` : ''

  const copyLink = async () => {
    let ok = false
    try {
      await navigator.clipboard.writeText(shareUrl)
      ok = true
    } catch {
      // запасной вариант для сред без clipboard API
      try {
        const el = document.createElement('textarea')
        el.value = shareUrl
        el.style.position = 'fixed'
        el.style.opacity = '0'
        document.body.appendChild(el)
        el.select()
        ok = document.execCommand('copy')
        document.body.removeChild(el)
      } catch {
        ok = false
      }
    }
    if (ok) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
      toast({
        title: 'Ссылка скопирована',
        description: 'Отправьте её коллеге в мессенджер или почту',
        duration: 3000,
      })
    } else {
      toast({
        title: 'Не удалось скопировать',
        description: 'Выделите ссылку в поле и скопируйте вручную',
        duration: 4000,
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto erp-scroll sm:max-w-lg [&>*]:min-w-0">
        {open && (
          <>
            <DialogHeader>
              <div className="flex items-center gap-3 pr-6">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                  <Share2 className="h-5 w-5" />
                </span>
                <div>
                  <DialogTitle className="text-lg">Поделиться макетом</DialogTitle>
                  <DialogDescription>
                    Отправьте коллеге ссылку или готовые файлы — установка не нужна
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            {/* Ссылка на просмотр */}
            <section aria-label="Ссылка на просмотр">
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Ссылка на просмотр
              </p>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Link2 className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    readOnly
                    value={shareUrl}
                    onFocus={(e) => e.currentTarget.select()}
                    className="h-10 pl-9 pr-16 font-mono text-sm"
                    aria-label="Ссылка на макет"
                  />
                  <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
                    URL
                  </kbd>
                </div>
                <Button
                  onClick={copyLink}
                  className={cn(
                    'h-10 gap-2 bg-emerald-600 px-4 font-semibold text-white hover:bg-emerald-700',
                    copied && 'bg-zinc-700 hover:bg-zinc-700'
                  )}
                >
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                  {copied ? 'Скопировано' : 'Копировать'}
                </Button>
              </div>
              <div className="mt-2.5 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2.5 dark:border-emerald-900 dark:bg-emerald-950/40">
                <LogIn className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                <p className="text-xs leading-relaxed text-emerald-800 dark:text-emerald-300">
                  Коллега увидит экран входа — достаточно нажать «Демо-вход гостем».
                  Ссылка активна, пока запущен этот проект.
                </p>
              </div>
            </section>

            <Separator />

            {/* Скачиваемые артефакты */}
            <section aria-label="Скачать файлы">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Или отправьте файлом
              </p>
              <div className="space-y-2">
                {DOWNLOADS.map((d) => (
                  <div
                    key={d.href}
                    className="flex items-center gap-3 rounded-xl border border-zinc-200 p-3 transition-colors hover:border-emerald-300 hover:bg-emerald-50/40 dark:border-zinc-800 dark:hover:border-emerald-800 dark:hover:bg-emerald-950/20"
                  >
                    <span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-lg', d.tone)}>
                      <d.icon className="h-5 w-5" />
                    </span>
                    <div className="min-w-0 flex-1 overflow-hidden">
                      <div className="flex min-w-0 items-center gap-2">
                        <p className="truncate text-sm font-semibold">{d.title}</p>
                        {d.badge && (
                          <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300">
                            {d.badge}
                          </span>
                        )}
                      </div>
                      <p className="truncate text-xs text-muted-foreground">{d.desc}</p>
                    </div>
                    <a
                      href={d.href}
                      download={d.fileName}
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 text-sm font-medium transition-colors hover:border-emerald-400 hover:bg-emerald-50 hover:text-emerald-700 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-emerald-700 dark:hover:bg-emerald-950/40 dark:hover:text-emerald-300"
                    >
                      <Download className="h-4 w-4" />
                      <span className="hidden sm:inline">Скачать</span>
                    </a>
                  </div>
                ))}
              </div>
            </section>

            {/* Как запустить локально */}
            <details className="group rounded-xl border border-zinc-200 dark:border-zinc-800">
              <summary className="flex cursor-pointer select-none items-center gap-2 px-3.5 py-3 text-sm font-medium transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900 [&::-webkit-details-marker]:hidden">
                <TerminalSquare className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                Как запустить у себя
                <ChevronDown className="ml-auto h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="border-t border-zinc-200 px-3.5 py-3 dark:border-zinc-800">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  Понадобится <span className="font-mono font-semibold text-foreground">bun</span> (или npm/pnpm).
                  Распакуйте ZIP и выполните:
                </p>
                <pre className="erp-scroll mt-2 overflow-x-auto rounded-lg bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-100">
                  <code>{`bun install
bun run dev`}</code>
                </pre>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                  Откроется <span className="font-mono">http://localhost:3000</span> — макет работает
                  офлайн, все данные демо и лежат в коде.
                </p>
              </div>
            </details>

            <p className="flex items-center gap-1.5 pb-1 text-[11px] text-muted-foreground">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
              Это макет: в нём нет реальных данных и доступов — делиться можно свободно.
            </p>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
