import { NotFoundError, apiGet, apiPost, isMockMode } from './client'
import type { OpportunityDetail, OpportunityFilters, OpportunityList, ResearchResult } from '../types/opportunities'
import { mockOpportunityDetail, mockOpportunityList, mockResearch } from '../mocks/opportunities'

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '' && value !== false) search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function opportunityPath(ticker: string): string {
  return `/opportunities/${encodeURIComponent(ticker)}`
}

export async function fetchOpportunities(filters: OpportunityFilters = {}): Promise<OpportunityList> {
  if (isMockMode()) return mockOpportunityList(filters)
  return apiGet<OpportunityList>(`/api/opportunities${query({ ...filters, page_size: 500 })}`)
}

export async function fetchOpportunity(ticker: string): Promise<OpportunityDetail> {
  if (isMockMode()) {
    const detail = mockOpportunityDetail(ticker)
    if (!detail) throw new NotFoundError(`Mock opportunity not found: ${ticker}`)
    return detail
  }
  return apiGet<OpportunityDetail>(`/api/opportunities/${encodeURIComponent(ticker)}`)
}

/** Manual AI research. Ineligible markets and unavailable providers resolve with an explicit status. */
export async function researchOpportunity(ticker: string, force = false): Promise<ResearchResult> {
  if (isMockMode()) return mockResearch(ticker)
  return apiPost<ResearchResult>(`/api/opportunities/${encodeURIComponent(ticker)}/research${query({ force })}`)
}
