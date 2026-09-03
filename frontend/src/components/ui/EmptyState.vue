<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
withDefaults(defineProps<{ title?: string; description?: string }>(), {})
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border py-16 text-center">
    <p class="text-sm font-semibold text-foreground">{{ title ?? t('ui.emptyTitle') }}</p>
    <p class="max-w-sm text-sm text-muted-foreground">{{ description ?? t('ui.emptyDescription') }}</p>
    <slot />
  </div>
</template>
