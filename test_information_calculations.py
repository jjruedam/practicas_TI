import math
from collections import Counter
from pathlib import Path

import numpy as np

from EventExperiment import (
    EventConfig,
    EventExperiment,
    SimpleEventConfig,
    _pairwise_context_mutual_information,
)


def test_word_distribution_uses_relative_frequencies_of_full_words():
    experiment = EventExperiment([0, 1, 1, 0, 1], word_length=2)

    assert experiment.counts == Counter({(0, 1): 2, (1, 1): 1, (1, 0): 1})
    assert experiment.total_count == 4
    assert experiment.word_distribution == {
        (0, 1): 0.5,
        (1, 1): 0.25,
        (1, 0): 0.25,
    }
    assert experiment._marginal_prob((0, 1)) == 0.5
    assert experiment._marginal_prob((0,)) == 0.5
    assert experiment._marginal_prob(()) == 1.0


def test_binary_word_counts_keep_recording_boundaries():
    recordings = np.array(
        [
            [[0], [1], [1]],
            [[1], [0], [0]],
        ],
        dtype=np.uint8,
    )

    counts = EventExperiment._binary_recording_word_counts(
        recordings, 0, word_length=2
    )

    assert counts == Counter({(0, 1): 1, (1, 1): 1, (1, 0): 1, (0, 0): 1})
    assert sum(counts.values()) == 4


def test_pairwise_mi_and_directional_normalizations_match_empirical_formula():
    events = np.array([[[0, 0], [0, 0], [1, 0], [1, 1]]], dtype=np.uint8)
    left_counts = EventExperiment._binary_recording_word_counts(
        events, 0, word_length=1
    )
    right_counts = EventExperiment._binary_recording_word_counts(
        events, 1, word_length=1
    )
    left_entropy = EventExperiment._entropy_from_counts(left_counts)
    right_entropy = EventExperiment._entropy_from_counts(right_counts)

    joint_counts = Counter(zip(events[0, :, 0], events[0, :, 1]))
    joint_entropy = EventExperiment._entropy_from_counts(joint_counts)
    expected_mi = left_entropy + right_entropy - joint_entropy

    mutual_information = _pairwise_context_mutual_information(
        events, left_channel=0, right_channel=1, context_length=1
    )
    normalized_by_left = mutual_information / left_entropy
    normalized_by_right = mutual_information / right_entropy

    expected_right_entropy = -(0.75 * math.log2(0.75) + 0.25 * math.log2(0.25))
    assert left_entropy == 1.0
    assert math.isclose(right_entropy, expected_right_entropy, abs_tol=1e-12)
    assert math.isclose(mutual_information, expected_mi, abs_tol=1e-12)
    assert math.isclose(mutual_information, 0.31127812445913283, abs_tol=1e-12)
    assert math.isclose(normalized_by_left, mutual_information, abs_tol=1e-12)
    assert math.isclose(normalized_by_right, 0.3836885465963443, abs_tol=1e-12)
    assert normalized_by_right > normalized_by_left


def test_manager_experiment_stores_normalization_by_the_matching_channel(tmp_path):
    class FakeManager:
        channel_names = ["LP", "VD"]
        filename = "fixture.txt"
        n_recordings = 1
        global_mean_max = None

        def __init__(self, data_dir: Path):
            self.data_dir = data_dir

        @staticmethod
        def discretize_recording(index, event_time_window, acceptance_threshold):
            return np.array(
                [[0, 0], [0, 0], [1, 0], [1, 1]],
                dtype=np.uint8,
            )

    experiment = EventExperiment(
        manager=FakeManager(tmp_path),
        config=EventConfig(
            word_length=1,
            simple_event=SimpleEventConfig(
                window=0.1,
                thresholds=(1.0, 1.0),
            ),
        ),
    )
    pair = ("LP", "VD")
    mutual_information = experiment.pairwise_mutual_information[pair]
    entropies = {
        channel: channel_experiment.block_entropy
        for channel, channel_experiment in experiment.channel_experiments.items()
    }
    normalized = experiment.pairwise_normalized_mutual_information[pair]

    assert math.isclose(mutual_information, 0.31127812445913283, abs_tol=1e-12)
    assert math.isclose(normalized["LP"], mutual_information / entropies["LP"])
    assert math.isclose(normalized["VD"], mutual_information / entropies["VD"])
