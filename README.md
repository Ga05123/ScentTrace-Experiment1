# ScentTrace — Experiment 1

GUI and web trajectory annotation for AgentNet, OM2W, OSWorld and WebChain.

**Workflow:** raw actions → repeat-operation merge → Local Goal → continuous Alignment → Operator.

## Quick start

Requires Git and Python 3.10+. On macOS/Linux, use `python3` if needed.

```bash
git clone https://github.com/Ga05123/ScentTrace-Experiment1.git
cd ScentTrace-Experiment1
python e1_quickstart.py setup
```

`setup` downloads and checks the dataset, creates the environment and installs dependencies. [Dataset](https://drive.google.com/drive/folders/1FPJTMRfneLGu63whG1MxP60Hca3S-UwG).

## Annotation

Test one complete trajectory:

```bash
python e1_quickstart.py probe
```

Enter the API URL, model ID and key when prompted. The model must support images and JSON Schema output. Review the trial results before running the full dataset.

The terminal shows task and stage progress, elapsed time and estimated time remaining. Estimates update as responses arrive and G4 requests are generated; detailed output is saved to `terminal.log` in the batch directory.

Annotate all 120 trajectories and export the results:

```bash
python e1_quickstart.py run
python e1_quickstart.py pack
```

Exported ZIP files are saved in `_e1_work/export/`. Annotation results are in `framework/runs/`.

## Framework

Code, Prompts, Codebooks and schemas are in [`framework/`](framework/). Defaults: 10 target Units with 2 context Units per side, image longest edge 1536, JPEG quality 95 and concurrency 4.

For direct CLI usage: `python framework/run_batch.py --help`. See [validation notes](docs/VALIDATION_AND_LIMITATIONS.md) for known limitations.
