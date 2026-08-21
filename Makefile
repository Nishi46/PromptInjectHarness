# S7-04. `make reproduce` re-runs the exact local-only sweep behind
# `results/static_baseline.md` (`configs/static_sweep.yaml` -- already
# `ollama`-only/digest-pinned, no API keys, 216 episodes -- reused as-is
# rather than a new smaller config; `docs/notes/release.md`'s S7-04
# section explains why a separate config would risk corrupting that real
# results file) and regenerates the table from the resulting trace DB.
#
# Real, measured wall-clock: 7-10 minutes at the CLI's default
# `--concurrency 4` -- two independent live full runs in clean git
# worktrees landed at 6:50 and 10:02 (docs/notes/release.md's S7-04
# verification log has the exact commands and both runs' output). $0:
# every model here is local Ollama. Idempotent: a second `make reproduce`
# against the same trace DB finishes in ~3 seconds (config-hash
# resumability, `sweep/runner.py::_config_hash` -- confirmed live, not
# assumed).
#
# Prerequisites (not run by this target -- see the README's own
# "Reproducing these results" section, which also covers a real Python-
# version pitfall found while verifying this target: plain `python3` on
# some machines resolves to too old a Python for this project):
#   python3.11 -m venv .venv && source .venv/bin/activate
#   pip install -e ".[dev]"
#   ollama pull llama3.2:3b
#   ollama pull llama3.1:latest
# ...and a running local Ollama server.

.PHONY: reproduce
reproduce: check-prereqs
	python -m injection_pareto run configs/static_sweep.yaml
	python scripts/generate_static_baseline.py

# Fastest possible real check that the harness works end to end (1
# episode, ~seconds) -- not a substitute for `reproduce`, just a quick
# sanity check before committing to the full ~7-10 minute run.
.PHONY: smoke
smoke: check-prereqs
	python -m injection_pareto run configs/smoke.yaml

.PHONY: check-prereqs
check-prereqs:
	@python -c "import injection_pareto" 2>/dev/null || \
		(echo "injection_pareto isn't installed -- run: pip install -e '.[dev]'" && exit 1)
	@command -v ollama >/dev/null 2>&1 || \
		(echo "ollama isn't on PATH -- install it first: https://ollama.com" && exit 1)
	@ollama list 2>/dev/null | grep -q "llama3.2:3b" || \
		(echo "llama3.2:3b isn't pulled -- run: ollama pull llama3.2:3b" && exit 1)
	@ollama list 2>/dev/null | grep -q "llama3.1:latest" || \
		(echo "llama3.1:latest isn't pulled -- run: ollama pull llama3.1:latest" && exit 1)
