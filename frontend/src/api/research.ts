import { NotFoundError, apiGet, isMockMode } from './client'
import type { ExperimentDetail, ExperimentList, ResearchCategory } from '../types/research'
import { mockExperimentDetail, mockExperimentList } from '../mocks/research'

export async function fetchResearchExperiments(category: ResearchCategory): Promise<ExperimentList> {
  if (isMockMode()) return mockExperimentList(category)
  return apiGet<ExperimentList>(`/api/research/experiments?category=${category}`)
}

export async function fetchResearchExperiment(id: string): Promise<ExperimentDetail> {
  if (isMockMode()) {
    const found = mockExperimentDetail(id)
    if (!found) throw new NotFoundError(`404 Unknown experiment ${id}`)
    return found
  }
  return apiGet<ExperimentDetail>(`/api/research/experiments/${encodeURIComponent(id)}`)
}

export function experimentPath(id: string): string {
  return `/research/${encodeURIComponent(id)}`
}
