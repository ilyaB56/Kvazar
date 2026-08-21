<script setup lang="ts">
import ruRu from 'element-plus/es/locale/lang/ru'
</script>

<template>
  <el-config-provider :locale="ruRu">
    <router-view />
  </el-config-provider>
</template>
