"""Pinned hashes and documented tolerances for the ITF singles study."""

from __future__ import annotations

from pathlib import Path

from ..core import PROTOCOLS, REPO_ROOT

RESEARCH_ONLY = True
NO_BET = True

COMPETITIONS = ("k_kxitfmatch", "k_kxitfwmatch")

LISTING_GZ = REPO_ROOT / "docs" / "research" / "prospective_performance_review_2026-10-21_inputs.tsv.gz"
LISTING_PAYLOAD_SHA256 = "53abb22116a0a4194c92097975387914a02d4747ed95a42f4c38a4edce1a51c8"
LISTING_GZ_SHA256 = "7473baadeb28a34ccf7912275bc49335243c1f02104a7188388760282fd183d5"

CAPTURE_PROTOCOL = "sports_phase2_capture_v2"
PHASE1_PROTOCOL = "sports_phase1_v2"
PHASE2_PROTOCOL = "sports_phase2_v1"

CAPTURE_PROTOCOL_SHA256 = "6fce0fac0623241f373ec2f277c167030075d2a8af73bf1540681f56c5fb41ad"
PHASE1_PROTOCOL_SHA256 = "66283d9e2a6b679d4391a53d195d122269fbeed62a4c76f24a77e7dae4d0f4c6"
PHASE2_PROTOCOL_SHA256 = "b95e662b233931caafbedfa16b04081d3c9ca01a2e43abfafa0c81867c81cb39"

FROZEN_EVENTS_SHA256 = {
    "k_kxitfmatch": "910755b9c55ddedb3761e83f51e6808c7402b3e054b647c59e5651c7f887c81e",
    "k_kxitfwmatch": "543116b42078b0d4de41478e53b9db268545ee4bc9738b8f5dce23baeaab3a7b",
}
FROZEN_CONTRACTS_SHA256 = {
    "k_kxitfmatch": "e39dd310fed0ed01bf60b748220f2d8e51797f30c4950f8ab1f0f6960cf6da8d",
    "k_kxitfwmatch": "109420609600205fcebda0a079410821f437a7ff8ca19e20eaf38e1c354eb495",
}
INVENTORY_SHA256 = "579981f783cb34919a9f843b27b9d34b770751c34a0ef5aecafc33bb43eb78aa"

DIAGNOSIS_N_EVENTS = 2828
DIAGNOSIS_N_CONTRACTS = 4695
DIAGNOSIS_ITF_CONTRIB = {
    "k_kxitfmatch": 0.007064022331,
    "k_kxitfwmatch": 0.0106764719754,
}
DIAGNOSIS_ITF_EVENTS = {"k_kxitfmatch": 376, "k_kxitfwmatch": 436}
REVIEW_CUTOFF = "2026-10-21T20:38:39.935499Z"

# Documented reconstruction agreement: Elo.prob vs saved frozen p after contract-side mapping.
P_AGREE_TOL = 1e-12

SNAP = REPO_ROOT / "data" / "results" / "prospective_review_asof"
SCORE_LIVE_PREFIX = "data/sports/phase2/capture_v2_scores/"
CAP_LIVE_PREFIX = "data/sports/phase2/capture_v2/"

DOCS = REPO_ROOT / "docs" / "research"
STUDY_STEM = "sports_itf_challenger_study_v1"
CAPTURE_STEM = "sports_itf_challenger_capture_v1"
CHALLENGER_DIR = Path(__file__).resolve().parent
PROTOCOL_PATH = CHALLENGER_DIR / "protocol_itf_singles_v1.json"
PROTOCOLS_DIR = PROTOCOLS

# Isolated capture wrappers. v1 is frozen; the active CLI uses v2. Do not reuse CAPTURE_PROTOCOL
# (that name is the live sports_phase2_capture_v2 protocol imported by inputs.py).
CHALLENGER_CAPTURE_V1 = "sports_itf_singles_challenger_capture_v1"
CHALLENGER_CAPTURE_V2 = "sports_itf_singles_challenger_capture_v2"
CHALLENGER_CAPTURE_V1_PATH = CHALLENGER_DIR / "protocol_itf_singles_capture_v1.json"
CHALLENGER_CAPTURE_V2_PATH = CHALLENGER_DIR / "protocol_itf_singles_capture_v2.json"
CHALLENGER_CAPTURE_V1_SHA256 = "414f259d1a86e5256c87844147e9b1d094416260ea130fc41d3fb6abd448b0d9"
ACTIVE_CAPTURE_PROTOCOL = CHALLENGER_CAPTURE_V2
ACTIVE_CAPTURE_PROTOCOL_PATH = CHALLENGER_CAPTURE_V2_PATH

# Collection wrapper (not the frozen helper). v2 freeze pins these files.
CAPTURE_CODE_FILES = ["history.py", "records.py", "capture.py", "constants.py", "outcomes.py"]
REPORT_CODE_FILES = ["report.py"]

CAPTURE_DIR = REPO_ROOT / "data" / "sports" / "challenger" / "itf_singles_v1"
EVIDENCE_ROOT = REPO_ROOT / "data" / "cache" / "sports" / "raw_challenger_itf_v1"

NOMINAL_HORIZON_MIN = 60
PAIRED_FROZEN_PARAMS = {"k": 24, "home": 0, "carry": 0.0, "mov": False, "gap_days": 120, "base": 1500.0}
INDIANAPOLIS_TZ = "America/Indiana/Indianapolis"
