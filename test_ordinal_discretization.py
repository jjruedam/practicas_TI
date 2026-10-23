import itertools
import json
import math

import numpy as np
import pytest

from EventExperiment import (
    OrdinalExperiment,
    ordinal_joint_distribution,
    ordinal_marginal_distributions,
    ordinal_word_distributions,
)


def test_ordinal_marginals_use_full_alphabet_and_order_plus_one_samples():
    data = {
        "VD": [0, 1, 2, 3],
        "LP": [3, 2, 1, 0],
    }

    vd_distribution, lp_distribution = ordinal_marginal_distributions(
        data, d=2
    )
    patterns = list(itertools.permutations(range(3)))

    assert len(vd_distribution) == math.factorial(3)
    assert vd_distribution[patterns.index((0, 1, 2))] == 1.0
    assert lp_distribution[patterns.index((2, 1, 0))] == 1.0
    assert np.isclose(vd_distribution.sum(), 1.0)
    assert np.isclose(lp_distribution.sum(), 1.0)


def test_ordinal_joint_distribution_is_sparse_and_preserves_recording_boundaries():
    data = {
        "VD": np.array([[0, 1, 2], [2, 1, 0]]),
        "LP": np.array([[2, 1, 0], [0, 1, 2]]),
    }

    joint = ordinal_joint_distribution(data, d=2)

    assert joint == {5: 0.5, 30: 0.5}
    assert math.isclose(sum(joint.values()), 1.0)


def test_ordinal_functions_reject_mismatched_channels_and_short_recordings():
    with pytest.raises(ValueError, match="same shape"):
        ordinal_marginal_distributions(
            {"VD": [1, 2, 3], "LP": [1, 2, 3, 4]},
            d=2,
        )

    with pytest.raises(ValueError, match="at least 3 samples"):
        ordinal_joint_distribution(
            {"VD": [1, 2], "LP": [2, 1]},
            d=2,
        )

def test_ordinal_functions_require_positive_integer_order():
    with pytest.raises(ValueError, match="at least 1"):
        ordinal_marginal_distributions(
            {"VD": [1, 2], "LP": [2, 1]},
            d=0,
        )

    with pytest.raises(TypeError, match="integer"):
        ordinal_joint_distribution(
            {"VD": [1, 2, 3], "LP": [3, 2, 1]},
            d=1.5,
        )


def test_ordinal_experiment_saves_entropy_and_information_metrics(tmp_path):
    class FakeManager:
        channel_names = ["LP", "VD"]
        filename = "recording.txt"
        data_dir = tmp_path

        @staticmethod
        def mean_per_window(n):
            assert n == 1
            return {
                "VD": np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
                "LP": np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]),
            }

    experiment = OrdinalExperiment(FakeManager(), d=1, n=1)

    assert experiment.channel_entropy == {"VD": 1.0, "LP": 1.0}
    assert math.isclose(experiment.joint_entropy, 1.0)
    assert math.isclose(experiment.mutual_information, 1.0)
    assert experiment.normalized_mutual_information == {"VD": 1.0, "LP": 1.0}

    payload = json.loads(experiment.output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "ordinal"
    assert payload["config"]["ordinal_order"] == 1
    assert payload["config"]["pattern_length"] == 2
    assert payload["config"]["word_length"] == 1
    assert payload["metrics"]["mutual_information_bits"] == 1.0
    assert payload["metrics"]["channel_entropy_bits"] == {
        "VD": 1.0,
        "LP": 1.0,
    }


def test_ordinal_experiment_uses_unique_json_paths(tmp_path):
    class FakeManager:
        channel_names = ["VD", "LP"]
        filename = "recording.txt"
        data_dir = tmp_path

        @staticmethod
        def mean_per_window(n):
            assert n == 1
            return {
                "VD": np.array([[0.0, 1.0, 0.0]]),
                "LP": np.array([[0.0, 1.0, 0.0]]),
            }

    first = OrdinalExperiment(FakeManager(), d=1, n=1)
    second = OrdinalExperiment(FakeManager(), d=1, n=1)

    assert first.output_path != second.output_path
    assert first.output_path.exists()
    assert second.output_path.exists()


def test_ordinal_experiment_requires_lp_and_vd_channels(tmp_path):
    class FakeManager:
        channel_names = ["LP"]
        filename = "recording.txt"
        data_dir = tmp_path

    with pytest.raises(ValueError, match="both 'VD' and 'LP'"):
        OrdinalExperiment(FakeManager(), d=1)


def test_ordinal_experiment_calculates_metrics_for_pattern_word_length(tmp_path):
    class FakeManager:
        channel_names = ["VD", "LP"]
        filename = "recording.txt"
        data_dir = tmp_path

        @staticmethod
        def mean_per_window(n):
            assert n == 1
            signal = np.array([[0.0, 1.0, 0.0, 1.0, 0.0]])
            return {"VD": signal, "LP": signal}

    experiment = OrdinalExperiment(
        FakeManager(),
        d=1,
        n=1,
        word_length=2,
    )

    expected_entropy = -(2 / 3 * math.log2(2 / 3) + 1 / 3 * math.log2(1 / 3))
    assert math.isclose(experiment.channel_entropy["VD"], expected_entropy)
    assert math.isclose(experiment.channel_entropy["LP"], expected_entropy)
    assert math.isclose(experiment.joint_entropy, expected_entropy)
    assert math.isclose(experiment.mutual_information, expected_entropy)
    assert experiment.normalized_mutual_information == {"VD": 1.0, "LP": 1.0}

    payload = json.loads(experiment.output_path.read_text(encoding="utf-8"))
    assert payload["config"]["word_length"] == 2
    assert "-word-2" in experiment.output_path.name


def test_ordinal_word_distributions_preserve_recording_boundaries():
    recordings = np.array(
        [
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 1.0],
        ]
    )
    marginals, joint = ordinal_word_distributions(
        {"VD": recordings, "LP": recordings},
        d=1,
        word_length=2,
    )

    assert marginals["VD"] == {(0, 1): 0.5, (1, 0): 0.5}
    assert marginals["LP"] == marginals["VD"]
    assert joint == {
        ((0, 1), (0, 1)): 0.5,
        ((1, 0), (1, 0)): 0.5,
    }


def test_ordinal_word_distributions_reject_too_short_contexts():
    with pytest.raises(ValueError, match="too short"):
        ordinal_word_distributions(
            {"VD": [0.0, 1.0], "LP": [0.0, 1.0]},
            d=1,
            word_length=2,
        )
