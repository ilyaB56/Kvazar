<script setup lang="ts" generic="T">
// Пагинируемый список (полировка производительности, 2026-09-16):
// первая порция pageSize (50), infinite scroll по sentinel,
// «Загрузить ещё», «Загрузить все (N)» (подтверждение при N > threshold),
// произвольное число строк, счётчик «Показано X из Y».
// Родитель рисует строки сам (слот items: T[]) — таблица, карточки,
// список организаций; контролы пагинации рендерит компонент.
// resetKey: смена значения (фильтр/таб) сбрасывает список и грузит с 0.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Button, Input, useToast } from '.'

export interface PageOf<T> {
  items: T[]
  total: number
}

const props = withDefaults(defineProps<{
  fetchPage: (offset: number, limit: number) => Promise<PageOf<T> | T[]>
  resetKey?: string | number
  pageSize?: number
  dangerThreshold?: number
}>(), {
  resetKey: '',
  pageSize: 50,
  dangerThreshold: 5000,
})

const emit = defineEmits<{ loaded: [total: number] }>()

const { t, n } = useI18n()
const toast = useToast()

const items = shallowRef<T[]>([])
const total = ref(0)
const initialLoading = ref(true)
const loadingMore = ref(false)
const customCount = ref('')

const shown = computed(() => items.value.length)
const moreAvailable = computed(() => shown.value < total.value)

function normalize(result: PageOf<T> | T[]): PageOf<T> {
  // старый формат (массив) — на случай эндпоинта без пагинации
  return Array.isArray(result) ? { items: result, total: result.length } : result
}

async function reload() {
  initialLoading.value = true
  try {
    const page = normalize(await props.fetchPage(0, props.pageSize))
    items.value = page.items
    total.value = page.total
    emit('loaded', total.value)
  } catch (error) {
    toast.apiError(error)
  } finally {
    initialLoading.value = false
  }
}

async function loadMore() {
  if (loadingMore.value || initialLoading.value || !moreAvailable.value) return
  loadingMore.value = true
  try {
    const page = normalize(await props.fetchPage(shown.value, props.pageSize))
    // shallowRef: обязателен НОВЫЙ массив — push на месте не триггерит
    items.value = [...items.value, ...page.items]
    total.value = page.total
    emit('loaded', total.value)
  } catch (error) {
    toast.apiError(error)
  } finally {
    loadingMore.value = false
  }
}

async function loadAll() {
  if (total.value > props.dangerThreshold
      && !window.confirm(t('pagination.loadAllConfirm', { n: n(total.value) }))) return
  initialLoading.value = true
  try {
    const page = normalize(await props.fetchPage(0, 0))
    items.value = page.items
    total.value = page.total
    emit('loaded', total.value)
  } catch (error) {
    toast.apiError(error)
  } finally {
    initialLoading.value = false
  }
}

async function loadCustom() {
  const count = Math.min(100_000, Math.max(1, Math.floor(Number(customCount.value))))
  if (!Number.isFinite(count) || customCount.value.trim() === '') return
  initialLoading.value = true
  try {
    const page = normalize(await props.fetchPage(0, count))
    items.value = page.items
    total.value = page.total
    emit('loaded', total.value)
  } catch (error) {
    toast.apiError(error)
  } finally {
    initialLoading.value = false
  }
}

// infinite scroll: sentinel под контролами; запас 600px — грузим,
// пока пользователь не долистал
const sentinel = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => { if (entries.some((e) => e.isIntersecting)) void loadMore() },
    { rootMargin: '600px' },
  )
  if (sentinel.value) observer.observe(sentinel.value)
  void reload()
})
onBeforeUnmount(() => observer?.disconnect())

watch(() => props.resetKey, () => { void reload() })

defineExpose({ reload })
</script>

<template>
  <div>
    <slot
      :items="items" :loading="initialLoading"
      :total="total" :shown="shown" :reload="reload"
    />

    <div v-if="!initialLoading" class="flex flex-wrap items-center justify-between gap-3 pt-1">
      <p class="text-xs text-muted-foreground">
        {{ t('pagination.shownOf', { shown: n(shown), total: n(total) }) }}
      </p>
      <div v-if="moreAvailable" class="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" :disabled="loadingMore" @click="loadMore">
          {{ t('pagination.loadMore') }}
        </Button>
        <Button variant="ghost" size="sm" :disabled="loadingMore" @click="loadAll">
          {{ t('pagination.loadAll', { n: n(total) }) }}
        </Button>
        <div class="flex items-center gap-1.5">
          <Input
            v-model="customCount" type="number" min="1" max="100000"
            class="h-8 w-[110px]" :placeholder="t('pagination.countPlaceholder')"
            @keydown.enter="loadCustom"
          />
          <Button variant="ghost" size="sm" :disabled="loadingMore" @click="loadCustom">
            {{ t('pagination.load') }}
          </Button>
        </div>
      </div>
    </div>
    <p v-if="loadingMore" class="pt-1 text-xs text-muted-foreground">
      {{ t('pagination.loading') }}
    </p>
    <div ref="sentinel" class="h-px w-full" />
  </div>
</template>
