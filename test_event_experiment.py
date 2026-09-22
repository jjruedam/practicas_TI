import math

import numpy as np
import pytest

from event_experiment import EventExperiment, mutual_information


def test_constant_signal_has_zero_entropy_and_zero_conditional_entropy():
    seq = [1, 1, 1, 1, 1, 1]
    exp = EventExperiment(seq, word_length=3)

    assert abs(exp.entropy_block) < 1e-12
    assert abs(exp.entropy_conditional) < 1e-12
    assert exp.prob((1,)) == 1.0


def test_periodic_signal_of_period_p_has_zero_conditional_entropy_for_long_enough_word():
    seq = [0, 1, 0, 1, 0, 1, 0, 1]
    exp = EventExperiment(seq, word_length=3)

    assert abs(exp.entropy_conditional) < 1e-12


def test_uniform_iid_has_conditional_entropy_equal_to_log_cardinality():
    rng = np.random.default_rng(0)
    seq = rng.integers(0, 4, size=20000)
    exp = EventExperiment(seq, word_length=4)

    expected = math.log2(4)
    assert abs(exp.entropy_conditional - expected) < 0.12


def test_markov_chain_conditional_entropy_matches_theoretical_rate():
    transition = np.array([[0.9, 0.1], [0.2, 0.8]], dtype=float)
    rng = np.random.default_rng(123)
    seq = np.zeros(20000, dtype=int)
    seq[0] = 0
    for i in range(1, len(seq)):
        seq[i] = rng.choice([0, 1], p=transition[seq[i - 1]])

    exp = EventExperiment(seq, word_length=2)

    pi = np.array([2 / 3, 1 / 3], dtype=float)
    theoretical = -np.sum(pi * np.sum(transition * np.log2(transition), axis=1, where=(transition > 0), initial=0.0))
    assert abs(exp.entropy_conditional - theoretical) < 0.15


def test_word_distribution_matches_stationary_transition_distribution():
    transition = np.array([[0.8, 0.2], [0.3, 0.7]], dtype=float)
    rng = np.random.default_rng(7)
    seq = np.zeros(5000, dtype=int)
    seq[0] = 0
    for i in range(1, len(seq)):
        seq[i] = rng.choice([0, 1], p=transition[seq[i - 1]])

    exp = EventExperiment(seq, word_length=1)
    pi = np.linalg.matrix_power(transition, 1000)[0]
    # stationary distribution for a 2-state chain
    stationary = np.array([0.6, 0.4], dtype=float)
    # the empirical marginal is close to the expected stationary law
    assert np.allclose(exp.word_distribution.values(), stationary, atol=0.12)


def test_chain_rule_reproduces_empirical_frequencies_for_full_words():
    seq = [0, 1, 1, 0, 1, 0, 1, 1, 0, 1]
    exp = EventExperiment(seq, word_length=2)

    observed = tuple(seq[0:3])
    empirical = exp.counts[observed] / exp.total_count
    assert abs(exp.chain_prob(observed) - empirical) < 1e-12


def test_identical_channels_have_mutual_information_equal_to_entropy_and_independent_channels_have_low_mi():
    rng = np.random.default_rng(123)
    x = np.array([0, 1, 1, 0, 1, 0, 0, 1, 1, 0], dtype=int)
    y_same = x.copy()
    y_ind = rng.integers(0, 2, size=5000, endpoint=False)
    x = rng.integers(0, 2, size=5000, endpoint=False)

    same_mi = mutual_information(x, x.copy())
    ind_mi = mutual_information(x, y_ind)
    x_entropy = EventExperiment(x, word_length=1).entropy_block

    assert abs(same_mi - x_entropy) < 1e-9
    assert ind_mi < 0.3


def test_save_and_load_roundtrip_for_config_and_metrics(tmp_path):
    seq = [0, 1, 1, 0, 1, 0]
    exp = EventExperiment(seq, word_length=2)
    destination = tmp_path / "experiment.json"

    exp.save(destination)
    restored = EventExperiment.load(destination)

    assert restored.word_length == exp.word_length
    assert restored.config.to_dict() == exp.config.to_dict()
    assert abs(restored.entropy_conditional - exp.entropy_conditional) < 1e-12
