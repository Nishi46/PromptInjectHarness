from pathlib import Path

import pytest

from injection_pareto.config import ConfigError, expand_run_specs, load_config

SMOKE_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "smoke.yaml"


def test_load_config_produces_expected_experiment_config() -> None:
    config = load_config(SMOKE_CONFIG_PATH)

    assert config.name == "smoke"
    assert len(config.models) == 1
    assert config.models[0].id == "L1"
    assert config.models[0].provider == "ollama"
    assert config.models[0].model == "llama3.2:3b"
    assert config.models[0].digest == "sha256:a80c4f17acd5"
    assert config.defenses == ["no_defense"]
    assert config.suites == ["workspace"]
    assert config.attacks == [None]
    assert config.tasks == {"workspace": ["user_task_0"]}
    assert config.injection_tasks == {}
    assert config.output.trace_db == "runs/local/smoke/trace.db"


def test_load_config_expands_to_one_run_spec_for_smoke() -> None:
    config = load_config(SMOKE_CONFIG_PATH)

    run_specs = expand_run_specs(config)

    assert len(run_specs) == 1
    spec = run_specs[0]
    assert spec.model.id == "L1"
    assert spec.defense == "no_defense"
    assert spec.suite == "workspace"
    assert spec.attack is None
    assert spec.tasks == ["user_task_0"]
    assert spec.injection_task is None


def test_expand_run_specs_produces_full_cartesian_product() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.defenses = ["no_defense", "spotlighting"]
    config.suites = ["workspace", "slack"]
    config.attacks = [None, "important_instructions"]
    config.tasks = {"workspace": ["user_task_0"], "slack": ["user_task_0", "user_task_1"]}
    config.injection_tasks = {"workspace": "injection_task_0", "slack": "injection_task_0"}

    run_specs = expand_run_specs(config)

    assert len(run_specs) == 1 * 2 * 2 * 2
    assert {(s.defense, s.suite, s.attack) for s in run_specs} == {
        (defense, suite, attack)
        for defense in config.defenses
        for suite in config.suites
        for attack in config.attacks
    }
    for spec in run_specs:
        assert spec.tasks == config.tasks[spec.suite]
        expected_injection_task = config.injection_tasks[spec.suite] if spec.attack else None
        assert spec.injection_task == expected_injection_task


def test_expand_run_specs_raises_when_suite_missing_from_tasks() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.suites = ["workspace", "slack"]  # "slack" has no entry in config.tasks

    with pytest.raises(ConfigError, match="no tasks configured for suite 'slack'"):
        expand_run_specs(config)


def test_expand_run_specs_raises_when_attacked_suite_missing_from_injection_tasks() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.attacks = [None, "important_instructions"]  # no config.injection_tasks entry

    with pytest.raises(ConfigError, match="no entry in config.injection_tasks"):
        expand_run_specs(config)


def test_load_config_missing_top_level_field_raises_clear_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("name: bad\nmodels: []\n")

    with pytest.raises(ConfigError, match="missing required top-level field"):
        load_config(bad_config)


def test_load_config_empty_models_list_raises_clear_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        "name: bad\nmodels: []\ndefenses: [no_defense]\nsuites: [workspace]\n"
        "attacks: [null]\ntasks:\n  workspace: [user_task_0]\noutput:\n  trace_db: x.db\n"
    )

    with pytest.raises(ConfigError, match="config.models must be a non-empty list"):
        load_config(bad_config)


def test_expand_run_specs_mcp_suite_filters_tasks_by_poisoned_cases_target_server() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.suites = ["mcp"]
    config.attacks = [None, "direct_instruction"]
    config.mcp_tasks = ["mcp_file_storage_0", "mcp_ticketing_0"]
    config.mcp_poisoned_cases = {
        "direct_instruction": "poison_direct_instruction_file_storage_list_files"
    }

    run_specs = expand_run_specs(config)

    benign_spec = next(s for s in run_specs if s.attack is None)
    attacked_spec = next(s for s in run_specs if s.attack == "direct_instruction")

    # benign pass keeps the full mcp_tasks list -- every task is a real
    # utility data point regardless of which server it mounts
    assert benign_spec.tasks == ["mcp_file_storage_0", "mcp_ticketing_0"]
    # attacked pass is filtered down to just the task(s) mounting the
    # poisoned case's target server ("file_storage") -- "mcp_ticketing_0"
    # would never actually see the injected description
    assert attacked_spec.tasks == ["mcp_file_storage_0"]
    assert attacked_spec.injection_task == "poison_direct_instruction_file_storage_list_files"


def test_expand_run_specs_raises_when_mcp_suite_missing_mcp_tasks() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.suites = ["mcp"]
    config.attacks = [None]

    with pytest.raises(ConfigError, match="config.mcp_tasks must be a non-empty list"):
        expand_run_specs(config)


def test_expand_run_specs_raises_when_mcp_attack_missing_from_mcp_poisoned_cases() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.suites = ["mcp"]
    config.attacks = ["direct_instruction"]
    config.mcp_tasks = ["mcp_file_storage_0"]

    with pytest.raises(ConfigError, match="no entry in config.mcp_poisoned_cases"):
        expand_run_specs(config)


def test_expand_run_specs_raises_when_no_mcp_task_matches_the_poisoned_cases_server() -> None:
    config = load_config(SMOKE_CONFIG_PATH)
    config.suites = ["mcp"]
    config.attacks = ["direct_instruction"]
    config.mcp_tasks = ["mcp_ticketing_0"]  # doesn't mount file_storage
    config.mcp_poisoned_cases = {
        "direct_instruction": "poison_direct_instruction_file_storage_list_files"
    }

    with pytest.raises(ConfigError, match="none of config.mcp_tasks mount server"):
        expand_run_specs(config)


def test_load_config_model_missing_field_raises_clear_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        "name: bad\nmodels:\n  - id: L1\n    provider: ollama\n"
        "defenses: [no_defense]\nsuites: [workspace]\nattacks: [null]\n"
        "tasks:\n  workspace: [user_task_0]\noutput:\n  trace_db: x.db\n"
    )

    with pytest.raises(ConfigError, match=r"models\[0\] is missing required field\(s\): model"):
        load_config(bad_config)
