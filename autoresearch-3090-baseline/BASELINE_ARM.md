# Single-Agent Baseline Arm

This repo is the single-agent comparison arm for the outer multi-agent orchestrator.

## Goal

Keep the fairness-critical training code aligned with [`autoresearch-3090`](./train.py) while running:

- the same GLM backend
- the same runtime Python
- the same dataset cache and tokenizer cache
- the same `Search/Replace` execution contract
- `worker_count = 1`
- `coordinator = false`

## Fairness Boundary

The files that matter for the training comparison are still:

- `train.py`
- `prepare.py`

This repo also includes a local launcher:

- `run_single_agent_baseline.py`

That launcher is convenience-only. It does not change the training code path and is not part of the editable search space.

## How To Run

From the project root:

```bash
python3 autoresearch-3090-baseline/run_single_agent_baseline.py --rounds 1
```

Or with the shared root-level launcher:

```bash
python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1
```

## Expected Behavior

The launcher will:

1. create a clean run workspace under `runs/`
2. measure the baseline `val_bpb` once
3. run one worker per round with the shared GLM agent backend
4. keep only strict improvements over the measured baseline

This repo is intended to stay in sync with `autoresearch-3090` on `train.py` / `prepare.py` whenever the 3090 baseline is updated.
