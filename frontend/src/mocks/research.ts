import type { ExperimentCard, ExperimentDetail, ExperimentList, ResearchCategory } from '../types/research'

const GENERATED = '2099-01-01T12:00:00Z'
const NOTE = 'Hub summary of the tracked protocol and docs. The protocol file is authoritative.'

const notCheckedCard = {
  kind: 'integrity',
  state: 'not_checked',
  label: 'Integrity',
  basis: 'Not checked for catalog cards; see the experiment page',
  reason: 'Not checked for catalog cards; see the experiment page',
}

const policy = (label: string, state: string) => ({ kind: 'policy', state, label, basis: 'protocol file' })

export const mockExperimentCards: ExperimentCard[] = [
  {
    id: 'weather.phase7_prospective_v1',
    category: 'weather',
    protocol_id: 'PHASE7_PROSPECTIVE_V1',
    title: 'Weather Phase 7: prospective GFS/HRRR shadow validation',
    version: 'v1',
    designation: 'prospective',
    modes: ['automatic_collection'],
    lifecycle: { value: 'collector unknown', basis: 'prospective task log cycle end lines' },
    frozen_at: '2026-10-02T17:09:09Z',
    period: { label: 'registered target dates', start: '2026-10-03', end: '2026-12-01', basis: 'protocol' },
    samples: [
      { label: 'Prediction records', value: null, basis: 'files in data/weather/phase7/records', reason: 'records directory not found' },
      { label: 'Registered target dates', value: 60, basis: 'collection.n_target_dates in the protocol' },
    ],
    results: [
      { label: 'Saved forecasts', kind: 'api', available: true, href: '/markets' },
      { label: 'Prediction records (local)', kind: 'local', available: false, reason: 'no prediction records on this machine' },
    ],
    results_available: false,
    status: {
      implementation: { kind: 'implementation', state: 'implemented', label: 'Phase 7 protocol', basis: 'Tracked implementation: research/weather/phase7.py' },
      evidence: [{ kind: 'evidence', state: 'missing', label: 'Final scores', basis: 'no score files on this machine', reason: 'no score files on this machine' }],
      operations: { kind: 'operations', state: 'unknown', label: 'Weather collector', basis: 'No prospective task log cycles on this machine', reason: 'No prospective task log cycles on this machine' },
      integrity: notCheckedCard,
      policy: [policy('Mode', 'RESEARCH_ONLY'), policy('Decision', 'NO_BET'), policy('Candidate', 'SHADOW_ONLY')],
    },
    summary_note: NOTE,
    error: null,
  },
  {
    id: 'weather.clinyc_transfer_v1',
    category: 'weather',
    protocol_id: 'CLINYC_TRANSFER_V1',
    title: 'Weather: KNYC to CLINYC settlement transfer',
    version: 'v1',
    designation: 'prospective',
    modes: ['historical_replay', 'automatic_collection'],
    lifecycle: { value: 'transfer experimental', basis: 'transfer_status in settlement_transfer_assessment.json' },
    frozen_at: '2026-09-25T21:24:11Z',
    period: { label: 'prospective validation', start: '2026-09-26', end: null, basis: 'hypothesis' },
    samples: [
      { label: 'Development pairs (pre-registration)', value: 42, basis: 'tracked hypothesis' },
      { label: 'Prospective paired events', value: 19, basis: 'settlement_transfer_assessment.json' },
    ],
    results: [{ label: 'Transfer assessment (local)', kind: 'local', available: true, path: 'data/results/settlement_transfer_assessment.json' }],
    results_available: true,
    status: {
      evidence: [{ kind: 'evidence', state: 'present', label: 'Transfer assessment', basis: 'artifact present and parseable; research conclusions not re-verified', as_of: GENERATED }],
      operations: { kind: 'operations', state: 'not_applicable', label: 'Operations', basis: 'Re-assessed from saved pairs', reason: 'Re-assessed from saved pairs' },
      integrity: notCheckedCard,
      policy: [policy('Mode', 'RESEARCH_ONLY')],
    },
    summary_note: NOTE,
    error: null,
  },
  {
    id: 'sports.phase2_capture_v2',
    category: 'sports',
    protocol_id: 'sports_phase2_capture_v2',
    title: 'Sports prospective capture (Phase 2 capture v2)',
    version: 'v2',
    designation: 'prospective',
    modes: ['manual_pilot', 'automatic_collection'],
    lifecycle: { value: 'collecting', basis: 'collector_status.json last cycle' },
    frozen_at: '2026-10-10T12:27:49Z',
    period: { label: 'capture period', start: '2026-10-10T12:29:40Z', end: '2026-10-15T18:00:00Z', basis: 'saved captures' },
    samples: [
      { label: 'Captured events', value: 12, basis: 'accepted capture records on this machine' },
      { label: 'Scored events', value: 8, basis: 'events with at least one finalized score' },
    ],
    results: [{ label: 'Pilot scores (tracked CSV)', kind: 'tracked', available: true, path: 'docs/research/sports_capture_v2_pilot_scores.csv' }],
    results_available: true,
    status: {
      operations: { kind: 'operations', state: 'recent_success', label: 'Sports collector', basis: 'collector_status.json last cycle', as_of: GENERATED },
      integrity: notCheckedCard,
      policy: [policy('Mode', 'RESEARCH_ONLY'), policy('Decision', 'NO_BET')],
    },
    summary_note: NOTE,
    error: null,
  },
  {
    id: 'sports.phase1_v1',
    category: 'sports',
    protocol_id: 'sports_phase1_v1',
    title: 'Sports Phase 1 v1: frozen models vs naive baselines',
    version: 'v1',
    designation: 'historical',
    modes: ['historical_replay'],
    lifecycle: { value: 'superseded by sports_phase1_v2', basis: 'supersedes field of the newer protocol' },
    frozen_at: '2026-10-09T01:28:49Z',
    period: { label: 'test period', start: '2026-01-01T00:00:00Z', end: '2026-10-06T00:00:00Z', basis: 'protocol splits' },
    samples: [{ label: 'Result rows (tracked summary CSV)', value: 303, basis: 'rows in docs/research/sports_phase1_results_v1.csv' }],
    results: [{ label: 'Results summary (CSV)', kind: 'tracked', available: true, path: 'docs/research/sports_phase1_results_v1.csv' }],
    results_available: true,
    status: { integrity: notCheckedCard, policy: [policy('Mode', 'RESEARCH_ONLY'), policy('Decision', 'NO_BET')] },
    summary_note: NOTE,
    error: null,
  },
]

export function mockExperimentList(category: ResearchCategory): ExperimentList {
  return {
    generated_at: GENERATED,
    category,
    mode: 'RESEARCH_ONLY',
    summary_note: NOTE,
    experiments: mockExperimentCards.filter((c) => c.category === category),
  }
}

export function mockExperimentDetail(id: string): ExperimentDetail | null {
  const card = mockExperimentCards.find((c) => c.id === id)
  if (!card) return null
  const historical = card.designation === 'historical'
  return {
    ...card,
    generated_at: GENERATED,
    mode: 'RESEARCH_ONLY',
    question: `Mock research question for ${card.title}.`,
    reference: 'Mock reference.',
    candidates: [{ id: 'frozen', label: 'frozen', description: 'Frozen model' }],
    cohort_rules: [{ name: 'strict', rule: 'Mock cohort rule' }],
    limitations: ['RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.'],
    mode_blocks: [
      { mode: 'historical_replay', label: 'Historical replay', applies: historical, description: historical ? 'Frozen retrospective evaluation.' : 'Not part of this protocol.', samples: historical ? card.samples : [] },
      { mode: 'manual_pilot', label: 'Manual pilot', applies: card.modes.includes('manual_pilot'), description: card.modes.includes('manual_pilot') ? 'Manual pilot captures.' : 'Not part of this protocol.', samples: [] },
      {
        mode: 'automatic_collection',
        label: 'Automatic collection',
        applies: card.modes.includes('automatic_collection'),
        description: card.modes.includes('automatic_collection') ? 'Scheduled collector.' : 'Not part of this protocol.',
        samples: historical ? [] : card.samples,
        operations: card.status.operations ?? null,
      },
    ],
    sources: [
      { label: 'Frozen protocol', path: 'research/sports/protocols/mock.json', tracked: true, exists: true, as_of: GENERATED },
      { label: 'Local metrics', path: 'data/results/mock/metrics.json', tracked: false, exists: false },
    ],
    frozen_settings: { protocol: card.protocol_id, research_only: true, no_bet: true, competitions: { omitted_for_display: true, entries: 12, size_bytes: 40960 } },
    links: [{ label: 'Results', href: card.category === 'sports' ? '/sports?tab=results' : '/markets' }],
  }
}
