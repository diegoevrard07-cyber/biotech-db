# Zero-Trial Company Triage Log

Date: 2026-06-12  
Context: 23 tickers returned zero CT.gov trials under primary company name search.

## Classifications

| Ticker | Name | Action | Rationale |
|--------|------|--------|-----------|
| ADAP | Adaptimmune Therapeutics | FIX_ALIAS | Active CAR-T biotech; CT.gov lists "Adaptimmune Limited" as lead sponsor |
| ADCT | ADC Therapeutics SA | FIX_ALIAS | Active ADC company; trials under "ADC Therapeutics" / "ADC Therapeutics AG" |
| ALKS | Alkermes | FIX_ALIAS | Active CNS/addiction biotech; sponsor variants include plc/Inc forms |
| ANAB | AnaptysBio | FIX_ALIAS | Active immunology biotech; trials under "AnaptysBio, Inc." |
| ANNX | Annexon | FIX_ALIAS | Active complement biotech; trials under "Annexon, Inc." |
| BCAB | BioAtla | FIX_ALIAS | Active conditionally-active biologics; "BioAtla, Inc." |
| BLTE | Belite Bio | FIX_ALIAS | Active Taiwan ADR; trials under "Belite Bio, Inc." |
| CGON | CG Oncology | FIX_ALIAS | Active bladder cancer biotech; "CG Oncology, Inc." |
| ETNB | 89bio | FIX_ALIAS | Active NASH biotech; legal name "89bio, Inc." not "89bio" |
| GBIO | Generation Bio | FIX_ALIAS | Active gene therapy platform; "Generation Bio Co." |
| HTGM | HTG Molecular Diagnostics | REMOVE_OUT_OF_SCOPE | Pure diagnostics / spatial transcriptomics tools |
| HYFT | MindWalk Holdings | UNKNOWN | Obscure holding company; sparse public trial footprint |
| IBRX | ImmunityBio | FIX_ALIAS | Active immuno-oncology; post-merger trials may list "NantKwest" |
| IMNM | Immunome | FIX_ALIAS | Active antibody discovery; "Immunome, Inc." |
| KRTX | Karuna Therapeutics | REMOVE_ACQUIRED | Acquired by Bristol-Myers Squibb (Dec 2023) |
| LQDA | Liquidia Corporation | FIX_ALIAS | Active pulmonary hypertension; "Liquidia Technologies, Inc." |
| MTVA | MetaVia | UNKNOWN | Ticker/company status uncertain; VERIFY listing |
| ORPH | Orphazyme | REMOVE_ACQUIRED | Bankruptcy / delisted (2022) |
| PCVX | Vaxcyte | FIX_ALIAS | Active vaccine biotech; "Vaxcyte, Inc." |
| TBIO | Telesis Bio | REMOVE_OUT_OF_SCOPE | DNA synthesis instruments (tools, not clinical-stage) |
| VYND | Vyant Bio | UNKNOWN | Small oncology services; limited lead-sponsor trials |
| XBIO | Xenetic Biosciences | FIX_ALIAS | Micro-cap biotech; "Xenetic Biosciences, Inc." |
| SURF | Surface Oncology | REMOVE_ACQUIRED | Wound down operations (2023) |

## Summary

| Action | Count | Tickers |
|--------|-------|---------|
| REMOVE_ACQUIRED | 3 | KRTX, ORPH, SURF |
| REMOVE_OUT_OF_SCOPE | 2 | HTGM, TBIO |
| FIX_ALIAS | 15 | ADAP, ADCT, ALKS, ANAB, ANNX, BCAB, BLTE, CGON, ETNB, GBIO, IBRX, IMNM, LQDA, PCVX, XBIO |
| UNKNOWN | 3 | HYFT, MTVA, VYND |

**Net CSV change:** 136 → 131 rows (−5 removed)

## Post re-ingest (2026-06-12)

After alias column + improved org-name matching (`ctgov` cache v2):

| Metric | Before triage | After |
|--------|---------------|-------|
| Companies in CSV/DB | 136 | 131 |
| Zero-trial companies | 23 | **4** |
| Total trials | 2,671 | **3,016** |
| Upcoming catalysts | 391 | **447** |
| Trial coverage | 83.1% | **96.9%** |

**Remaining zero-trial (4):**

| Ticker | Classification | Notes |
|--------|----------------|-------|
| HYFT | UNKNOWN_VERIFY_MANUALLY | As triaged |
| MTVA | UNKNOWN_VERIFY_MANUALLY | As triaged |
| GBIO | UNKNOWN_VERIFY_MANUALLY | Alias fix did not surface lead-sponsor trials |
| VSTM | UNKNOWN_VERIFY_MANUALLY | Verastem — may need manual sponsor review |
