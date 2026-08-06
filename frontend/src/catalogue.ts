import type { AssetType } from '@/types'

export const assetMeta: Record<
  AssetType,
  { label: string; english: string; description: string; color: string; softColor: string }
> = {
  paper: {
    label: '论文',
    english: 'Papers',
    description: '实验室原创论文与投稿历程',
    color: '#327353',
    softColor: '#e4f0e7',
  },
  dataset: {
    label: '数据集',
    english: 'Datasets',
    description: '研究数据资源、版本与说明',
    color: '#416fab',
    softColor: '#e7eef8',
  },
  literature: {
    label: '文献',
    english: 'Literature',
    description: '共享参考文献与批注资料',
    color: '#b47c1d',
    softColor: '#f8eedc',
  },
  project: {
    label: '项目',
    english: 'Projects',
    description: '研究计划、成员与关联成果',
    color: '#7658a8',
    softColor: '#eee8f6',
  },
  model: {
    label: '模型',
    english: 'Models',
    description: '模型版本、权重与评测记录',
    color: '#267c7e',
    softColor: '#e0f1ef',
  },
}

export const assetTypes = Object.keys(assetMeta) as AssetType[]

