'use client'

import Image from 'next/image'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ViewHeader, DraftNote } from './shared'
import {
  Building2,
  Download,
  Palette,
  Save,
  ShieldCheck,
  BellRing,
  Users2,
  DatabaseBackup,
  CheckCircle2,
  KeyRound,
  Lock,
} from 'lucide-react'
import { useState } from 'react'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  erpRoles,
  permModules,
  permValueMap,
  permissionMatrix,
  type PermValue,
  type RoleId,
} from '@/lib/erp-data'

const permCycle: Record<PermValue, PermValue> = {
  full: 'ro',
  ro: 'none',
  none: 'full',
}

function PermissionsMatrix() {
  const [matrix, setMatrix] = useState(permissionMatrix)
  const [dirty, setDirty] = useState(false)
  const { toast } = useToast()

  const cycle = (role: RoleId, moduleKey: string) => {
    if (role === 'admin') return
    setMatrix((prev) => ({
      ...prev,
      [role]: {
        ...prev[role],
        [moduleKey]: permCycle[prev[role][moduleKey]],
      },
    }))
    setDirty(true)
  }

  const applyPermissions = () => {
    setDirty(false)
    toast({
      title: 'Права применены',
      description: 'Пользователи получат новые доступы при следующем входе',
      duration: 3000,
    })
  }

  return (
    <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4 text-emerald-600" /> Доступы и роли
          </CardTitle>
          <CardDescription>
            Кликните по ячейке, чтобы переключить уровень доступа: R/W → R/O → закрыт
          </CardDescription>
        </div>
        <Button
          size="sm"
          variant={dirty ? 'default' : 'outline'}
          onClick={applyPermissions}
          disabled={!dirty}
          className={cn(
            'gap-1.5 text-xs',
            dirty && 'bg-emerald-600 text-white hover:bg-emerald-700'
          )}
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          {dirty ? 'Применить права' : 'Изменений нет'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Карточки ролей */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {erpRoles.map((r) => (
            <div
              key={r.id}
              className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-2.5 transition-colors hover:border-emerald-300 dark:border-zinc-800 dark:bg-zinc-900/50 dark:hover:border-emerald-800"
            >
              <div className="flex items-center gap-2">
                <span className={cn('h-2.5 w-2.5 shrink-0 rounded-sm', r.color)} />
                <p className="truncate text-xs font-semibold">{r.name}</p>
              </div>
              <p className="mt-1 line-clamp-2 text-[10px] leading-snug text-muted-foreground">{r.desc}</p>
              <p className="mt-1.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                {r.members} сотрудник{r.members % 10 === 1 && r.members !== 11 ? '' : r.members % 10 >= 2 && r.members % 10 <= 4 && (r.members < 12 || r.members > 14) ? 'а' : 'ов'}
              </p>
            </div>
          ))}
        </div>

        {/* Матрица прав */}
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <Table>
            <TableHeader>
              <TableRow className="bg-zinc-50/80 hover:bg-zinc-50/80 dark:bg-zinc-900/50">
                <TableHead className="min-w-[160px]">Роль</TableHead>
                {permModules.map((m) => (
                  <TableHead key={m.key} className="text-center text-xs">{m.label}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {erpRoles.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <span className="flex items-center gap-2 text-[13px] font-medium">
                      <span className={cn('h-2 w-2 shrink-0 rounded-full', r.color)} />
                      {r.name}
                      {r.id === 'admin' && <Lock className="h-3 w-3 text-muted-foreground" aria-label="Неизменяемая роль" />}
                    </span>
                  </TableCell>
                  {permModules.map((m) => {
                    const val = matrix[r.id][m.key]
                    const vm = permValueMap[val]
                    const locked = r.id === 'admin'
                    return (
                      <TableCell key={m.key} className="text-center">
                        <button
                          type="button"
                          onClick={() => cycle(r.id, m.key)}
                          disabled={locked}
                          title={vm.label}
                          aria-label={`${r.name} · ${m.label}: ${vm.label}${locked ? ' (неизменяемо)' : ''}`}
                          className={cn(
                            'inline-flex h-7 min-w-[52px] items-center justify-center rounded-md border text-[10px] font-bold transition-all',
                            locked && 'cursor-default border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900',
                            !locked && 'border-zinc-200 hover:border-emerald-400 hover:bg-emerald-50 active:scale-95 dark:border-zinc-700 dark:hover:border-emerald-700 dark:hover:bg-emerald-950/40',
                            vm.cls
                          )}
                        >
                          {vm.short}
                        </button>
                      </TableCell>
                    )
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        {/* Легенда */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
          <span className="font-medium">Легенда:</span>
          <span className="flex items-center gap-1.5"><span className="font-bold text-emerald-600 dark:text-emerald-400">R/W</span> полный доступ</span>
          <span className="flex items-center gap-1.5"><span className="font-bold text-amber-600 dark:text-amber-400">R/O</span> только чтение</span>
          <span className="flex items-center gap-1.5"><span className="font-bold text-zinc-400 dark:text-zinc-600">—</span> закрыт</span>
          <span className="ml-auto flex items-center gap-1"><Lock className="h-3 w-3" /> роль администратора неизменяема</span>
        </div>
      </CardContent>
    </Card>
  )
}

const brandVariants = [
  {
    src: '/brand/quasar-violet.png',
    name: 'Ядро · основной знак',
    note: 'Белое ядро, орбиты модулей и лучи интеграций — фирменный знак системы',
    tag: 'Выбран',
    main: true,
  },
  {
    src: '/brand/quasar-amber.png',
    name: 'Тёплое ядро',
    note: 'Янтарный аккреционный диск — вариант для презентаций и сайта',
    tag: null,
    main: false,
  },
  {
    src: '/brand/quasar-emerald.png',
    name: 'Изумрудная орбита',
    note: 'В тон интерфейса — иконки приложений и виджеты',
    tag: null,
    main: false,
  },
  {
    src: '/brand/quasar-mono.png',
    name: 'Монохром',
    note: 'Для документов, фавиконок и печати в один тон',
    tag: null,
    main: false,
  },
]

function BrandCard() {
  const { toast } = useToast()

  const handleDownload = (name: string) => {
    toast({
      title: 'Логотип скачивается',
      description: `Вариант «${name}» сохранится как PNG 1024×1024`,
      duration: 2500,
    })
  }

  const handleDownloadAll = () => {
    toast({
      title: 'Архив скачивается',
      description: 'quasar-logos.zip — 4 варианта в PNG + векторный SVG',
      duration: 2500,
    })
  }

  const handleDownloadSvg = () => {
    toast({
      title: 'Вектор скачивается',
      description: 'quasar-mark.svg — векторный знак, масштабируется без потерь',
      duration: 2500,
    })
  }

  return (
    <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
      <CardHeader className="pb-4">
        <div className="flex items-start gap-2">
          <Palette className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
          <div className="min-w-0">
            <CardTitle className="text-base">Бренд: логотип «Квазар»</CardTitle>
            <CardDescription>
              Знак — квазар: раскалённое ядро интеграционной платформы и орбиты изолированных модулей
            </CardDescription>
          </div>
        </div>
        <CardAction className="flex items-center gap-2 max-sm:col-start-1 max-sm:row-start-2 max-sm:justify-self-end">
          <a
            href="/brand/quasar-mark.svg"
            download
            onClick={handleDownloadSvg}
            aria-label="Скачать векторную версию знака в SVG"
            title="Скачать вектор (SVG)"
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-background px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-zinc-100 hover:text-violet-600 dark:border-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-violet-400"
          >
            <Download className="h-3.5 w-3.5" />
            <span className="whitespace-nowrap">SVG</span>
          </a>
          <a
            href="/brand/quasar-logos.zip"
            download
            onClick={handleDownloadAll}
            aria-label="Скачать все варианты логотипа одним ZIP-архивом"
            title="Скачать все варианты одним архивом"
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-background px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-zinc-100 hover:text-violet-600 dark:border-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-violet-400"
          >
            <Download className="h-3.5 w-3.5" />
            <span className="whitespace-nowrap">Скачать все (ZIP)</span>
          </a>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {brandVariants.map((v) => (
            <div
              key={v.src}
              className={cn(
                'group overflow-hidden rounded-xl border bg-zinc-950 transition-all',
                v.main
                  ? 'border-violet-500/50 shadow-md shadow-violet-500/10'
                  : 'border-zinc-200 hover:border-violet-500/30 dark:border-zinc-800'
              )}
            >
              <div className="relative aspect-square">
                <Image
                  src={v.src}
                  alt={`Логотип «Квазар» — вариант «${v.name}»`}
                  fill
                  sizes="(min-width: 1024px) 25vw, 50vw"
                  className="object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                />
                {v.tag && (
                  <span className="absolute left-2 top-2 rounded-full bg-violet-500 px-2 py-0.5 text-[10px] font-bold text-white shadow">
                    {v.tag}
                  </span>
                )}
              </div>
              <div className="space-y-1 border-t border-zinc-200 bg-card p-3 dark:border-zinc-800">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-xs font-semibold">{v.name}</p>
                  <a
                    href={v.src}
                    download
                    onClick={() => handleDownload(v.name)}
                    aria-label={`Скачать логотип «${v.name}» в PNG`}
                    title="Скачать PNG"
                    className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-zinc-100 hover:text-violet-600 dark:hover:bg-zinc-800 dark:hover:text-violet-400"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </a>
                </div>
                <p className="text-[11px] leading-snug text-muted-foreground">{v.note}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Символика знака */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-zinc-200 bg-zinc-50/60 px-3.5 py-2.5 text-[11px] text-muted-foreground dark:border-zinc-800 dark:bg-zinc-900/50">
          <span className="font-semibold text-foreground">Символика:</span>
          <span><span className="font-bold text-violet-600 dark:text-violet-400">ядро</span> — интеграционная платформа и ИИ</span>
          <span><span className="font-bold text-violet-600 dark:text-violet-400">орбиты</span> — модули в изолированном контуре</span>
          <span><span className="font-bold text-violet-600 dark:text-violet-400">точка на орбите</span> — подключаемая интеграция</span>
          <span><span className="font-bold text-violet-600 dark:text-violet-400">лучи</span> — связь с внешним миром</span>
        </div>
      </CardContent>
    </Card>
  )
}

function SettingsRow({
  title,
  description,
  children,
  defaultChecked,
}: {
  title: string
  description: string
  children?: React.ReactNode
  defaultChecked?: boolean
}) {
  const [on, setOn] = useState(!!defaultChecked)
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      {children ?? (
        <Switch checked={on} onCheckedChange={setOn} className="data-[state=checked]:bg-emerald-600" />
      )}
    </div>
  )
}

export function SettingsView() {
  const [saved, setSaved] = useState(false)
  const { toast } = useToast()

  const handleSave = () => {
    setSaved(true)
    toast({
      title: 'Настройки сохранены',
      description: 'Реквизиты организации и системные правила обновлены',
      duration: 3000,
    })
    setTimeout(() => setSaved(false), 2500)
  }

  return (
    <div className="space-y-6">
      <ViewHeader title="Настройки" subtitle="Параметры организации и системные правила">
        <Button
          size="sm"
          onClick={handleSave}
          className={cn(
            'gap-1.5 text-white transition-colors',
            saved ? 'bg-teal-600 hover:bg-teal-600' : 'bg-emerald-600 hover:bg-emerald-700'
          )}
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? 'Сохранено' : 'Сохранить'}
        </Button>
      </ViewHeader>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* Профиль организации */}
        <Card className="border-zinc-200 shadow-sm dark:border-zinc-800 xl:col-span-2">
          <CardHeader className="flex-row items-center gap-2 space-y-0 pb-4">
            <Building2 className="h-4 w-4 text-emerald-600" />
            <div>
              <CardTitle className="text-base">Организация</CardTitle>
              <CardDescription>Реквизиты и учётная политика</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="org-name">Название</Label>
                <Input id="org-name" defaultValue="ООО «ТехноПром»" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-inn">ИНН / КПП</Label>
                <Input id="org-inn" defaultValue="7712345678 / 771201001" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-dir">Директор</Label>
                <Input id="org-dir" defaultValue="Науменко В.В." />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-currency">Валюта учёта</Label>
                <Select defaultValue="rub">
                  <SelectTrigger id="org-currency">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="rub">₽ Рубль (RUB)</SelectItem>
                    <SelectItem value="usd">$ Доллар (USD)</SelectItem>
                    <SelectItem value="eur">€ Евро (EUR)</SelectItem>
                    <SelectItem value="cny">¥ Юань (CNY)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-tax">Система налогообложения</Label>
                <Select defaultValue="usn">
                  <SelectTrigger id="org-tax">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="usn">УСН, доходы − расходы</SelectItem>
                    <SelectItem value="osn">ОСНО</SelectItem>
                    <SelectItem value="patent">Патент</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-fiscal">Финансовый год</Label>
                <Input id="org-fiscal" defaultValue="01.01.2025 — 31.12.2025" />
              </div>
            </div>
            <Separator />
            <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
              <SettingsRow
                title="Автонумерация документов"
                description="Префикс ЗК-25, счётчик сбрасывается 01.01"
                defaultChecked
              />
              <SettingsRow
                title="Резервировать товар при заказе"
                description="Автоматически занимать остатки под новые заказы"
                defaultChecked
              />
              <SettingsRow
                title="Контролировать лимиты бюджетов"
                description="Блокировать заявки сверх лимита до согласования директором"
              />
            </div>
          </CardContent>
        </Card>

        {/* Правая колонка */}
        <div className="space-y-4">
          {/* Пользователь */}
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users2 className="h-4 w-4 text-emerald-600" /> Профиль
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-3">
                <Avatar className="h-12 w-12">
                  <AvatarFallback className="bg-emerald-100 text-emerald-700">НС</AvatarFallback>
                </Avatar>
                <div>
                  <p className="text-sm font-semibold">Науменко Владимир</p>
                  <p className="text-xs text-muted-foreground">Директор · Полные права</p>
                </div>
              </div>
              <Button variant="outline" size="sm" className="w-full">
                Редактировать профиль
              </Button>
            </CardContent>
          </Card>

          {/* Уведомления */}
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <BellRing className="h-4 w-4 text-emerald-600" /> Уведомления
              </CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-zinc-100 dark:divide-zinc-800">
              <SettingsRow title="Просроченные счета" description="Пуш + e-mail" defaultChecked />
              <SettingsRow title="Низкие остатки" description="При достижении минимума" defaultChecked />
              <SettingsRow title="Новые заказы" description="Менеджерам в чат" />
            </CardContent>
          </Card>

          {/* Безопасность */}
          <Card className="border-zinc-200 shadow-sm dark:border-zinc-800">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-emerald-600" /> Безопасность
              </CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-zinc-100 dark:divide-zinc-800">
              <SettingsRow title="Двухфакторная аутентификация" description="Для ролей с правом оплаты" defaultChecked />
              <SettingsRow title="Журнал действий" description="Хранить 3 года" defaultChecked />
              <SettingsRow title="Резервные копии" description="Ежедневно в 03:00 · 30 копий">
                <Button variant="outline" size="sm" className="gap-1.5">
                  <DatabaseBackup className="h-3.5 w-3.5" /> Скачать
                </Button>
              </SettingsRow>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Роли и права доступа */}
      <PermissionsMatrix />

      {/* Бренд системы: логотип «Квазар» */}
      <BrandCard />

      <DraftNote text="Макет: документооборот, интеграции (1С, банк-клиент, e-commerce), шаблоны доступов и аудит действий." />
    </div>
  )
}
