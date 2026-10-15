# BOUND-R3 validation and attribution

This is a code snapshot, not a declaration that Experiment 1 or its semantic validation is complete.

## Observed outcome — 2026-10-15

| Corpus | Native Units | G2 | G3 strict coverage | Accepted G4 |
|---|---:|---|---:|---:|
| AgentNet | 32 | complete | 32 | 32 |
| OM2W | 3 | complete | 3 | 3 |
| OSWorld | 223 | complete | 223 | 203 |
| WebChain | 6 | complete | 6 | 6 |
| Total | 264 | 4 tasks | 264 | 244 |

These previously exposed regression samples are not an independent confirmation set. All 79 offline tests passed. Full stage Codebooks, G4-C1/C2 and five anonymized examples, the current-Unit reason rule, actual native images, dynamic strict schemas, model identity, request hashes and implementation fingerprints were checked in genuine provider requests. The 22 frozen source files remained unchanged.

65 logical requests produced 63 accepted outputs and 2 terminal failures. Four initially invalid G3 responses were rejected by validation and corrected within the existing finite format-retry policy. All 67 provider responses, including initial invalid outputs, remain in the private local run archive.

OSWorld G4 Units 60–69 failed after HTTP 429/TPM rate limits; Units 126–135 failed with HTTP 503. The latter is conservatively recorded as outcome/billing unknown and was not resent. The 20 missing outputs are not UNRESOLVED labels. Known captured usage was 1,492,780 input and 19,044 output tokens; configuration-price estimate CNY 2.892679 excludes unknown 503 charges and is not an invoice.

## What can be attributed

- Input preparation and transport: no observed action/image/Unit-index misbinding; Codebooks and reason rules reached the provider.
- Provider availability: the 429 and 503 failures originated at the API service. They are not evidence against the Operator definitions.
- Model response: researcher review found 117 current-action/reason factual contradictions in 244 available G4 outputs, including descriptions of adjacent Units; 20 likely Codebook conflicts and 23 boundary disputes can overlap. Technical process diagnosis was sometimes INSPECT despite C1, and task-specific execution preparation/submission was inconsistently handled despite C2.
- Causal limit: a single model/configuration run cannot isolate intrinsic model capability from Prompt wording, evidence presentation or context interactions. It does not establish that the research framework or definitions are fully correct.
- Structural acceptance checks JSON/coverage/source binding; it cannot certify free-text factual claims or semantic judgments.

All semantic findings are Codex/GPT-assisted researcher review, not independent Human Gold, accuracy or F1. The comparison has 244 G4 pairs: 213 same and 31 changed. Label changes are not automatically improvements.

## Known runtime limitations retained in this snapshot

1. The frozen CLI may exit 0 while the batch checkpoint is PARTIAL_WITH_ERRORS. Inspect the checkpoint instead of accepting the process exit code as completion.
2. Exhausted 429 retries retain the terminal failure but not each earlier individual rejection detail. At least 20 rejections are directly verifiable; the log is not a full per-attempt ledger.
3. Provider outcomes or costs can remain unknown after a 503/timeout/network interruption. Do not automatically repeat such a paid request.
4. Original public traces can lack an independent final post-observation. A complete released record does not prove task success or natural search-bout termination.
5. The original offline suite depends on private native source fixtures and a sibling `EXPERIMENT_1_CURRENT` directory containing four original files for read-only AST/byte comparison. They are omitted from Git deliberately; the suite must not be claimed portable without them. Repository preparation found this location dependency (78 tests passed, one errored for a missing baseline path); supplying the original references made the isolated recheck pass. No runtime or test implementation changed.

No core code, Prompt, Codebook, Schema, source adapter, original response or label was revised to make this test appear to pass. Changing the framework or performing additional paid testing requires an explicit new version and applicable user approval. This upload performs neither.
