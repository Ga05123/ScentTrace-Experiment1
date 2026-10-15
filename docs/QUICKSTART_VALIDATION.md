# Experiment 1 Quickstart — scope and technical acceptance

## Cohort authorization

The user authorized the selected 120 native GUI trajectories (AgentNet 10, OM2W 40, OSWorld 60, WebChain 10; 40 task families) for **model annotation**. The original transfer is source-only; it must remain unchanged. R3 `run_batch.py prepare` generates derived `MODEL_ANNOTATION_COHORT` / `FULL_TRIAL` records and records each original `split` and `trial_role` in `BATCH_INDEX.jsonl`.

The authorization manifest `COHORT_AUTHORIZATION_120.json` fixes the official `FILE_MANIFEST.jsonl` hash and dataset counts. This is not Human Gold, independent confirmation data, a claim of semantic accuracy, or consent for unattended model costs. Paid inference requires the operator to explicitly execute `probe` or `run` and enter a provider API key.

## Technical checks

- Source: 12 official ZIPs are required; `unpack_and_verify.py` validates archive and extracted-file SHA256.
- Environment: Python 3.10+; `setup` installs a local venv, resolves portable native image paths, checks 120 sources and the authorization manifest. Public Google Drive access requires no account.
- One-trajectory technical chain: actual native OM2W screenshot transport through a **127.0.0.1 loopback HTTP fixture**, G2 → G3 → G4, immutable Alignment, resume without duplicate requests and raw invalid-response retention: PASS. This is not a real provider or a semantic-label test.
- Batch preparation: R3 derives only the batch-copy roles. The original public file is not rewritten.
- When model provider calls fail, the checkpoint can remain partial even if the underlying CLI exits with status 0; quickstart verifies `BATCH_EXECUTION_STATUS.json` before reporting success.
- Runtime traces, credentials and data remain outside Git (see `.gitignore`). `pack` exports only quickstart runs, plus manifests/Codebooks/Prompts/Schema and ZIP SHA256 sidecar.

## Pending before scientific acceptance

A real externally billed Gemini/GPT/Claude call was **not** made during this technical handoff without operator-provided credentials. Model-specific JSON Schema, native image support, actual provider-model identity, rate limits, token usage and semantic correctness must be verified on the chosen provider. No silent schema/text/image fallback is allowed.

The present 120-trajectory source set is exposed development / model-annotation cohort, not an independent blind holdout. Provider output may require further researcher/human semantic adjudication.
