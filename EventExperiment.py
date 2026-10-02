from __future__ import annotations

import json
import math
import warnings
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations, islice
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from DataManager import DataManager, SAMPLE_INTERVAL

class WordDistribution(dict):
    """Dictionary-like distribution whose values() returns a NumPy array."""

    def values(self):
        return np.asarray(list(super().values()), dtype=float)


@dataclass
class SimpleEventConfig:
    """Serializable simple-event configuration; `window` is in milliseconds."""

    name: str = "simple_event"
    window: float = 1
    stride: int = 1
    discretization: str = "spike_detection"
    thresholds: tuple[float, ...] = ()
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window": self.window,
            "stride": self.stride,
            "discretization": self.discretization,
            "thresholds": list(self.thresholds),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SimpleEventConfig":
        return cls(
            name=str(data.get("name", "simple_event")),
            window=float(data.get("window", 1)),
            stride=int(data.get("stride", 1)),
            discretization=str(data.get("discretization", "spike_detection")),
            thresholds=tuple(float(v) for v in data.get("thresholds", ())),
            version=int(data.get("version", 1)),
        )


@dataclass
class EventConfig:
    """Configuration for a channel experiment."""

    word_length: int = 1
    simple_event: SimpleEventConfig = field(default_factory=SimpleEventConfig)
    source_name: str | None = None
    version: int = 1
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_length": self.word_length,
            "simple_event": self.simple_event.to_dict(),
            "source_name": self.source_name,
            "version": self.version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventConfig":
        simple_event_data = data.get("simple_event", {})
        return cls(
            word_length=int(data.get("word_length", 1)),
            simple_event=SimpleEventConfig.from_dict(simple_event_data),
            source_name=data.get("source_name"),
            version=int(data.get("version", 1)),
            notes=str(data.get("notes", "")),
        )


class EventExperiment:
    """Compute Markov-style information-event statistics from a simple-event stream."""

    def __init__(
        self,
        sequence: Sequence[Any] | np.ndarray | None = None,
        manager: DataManager | None = None,
        word_length: int | None = None,
        config: EventConfig | Mapping[str, Any] | None = None,
    ):
        configured_word_length = (
            config.word_length
            if isinstance(config, EventConfig)
            else int(config.get("word_length", 1))
            if isinstance(config, Mapping)
            else 1
        )
        self.word_length = int(
            configured_word_length if word_length is None else word_length
        )
        if self.word_length < 1:
            raise ValueError("word_length must be >= 1.")

        if config is None:
            self.config = EventConfig(word_length=self.word_length)
        elif isinstance(config, EventConfig):
            self.config = config
            self.config.word_length = self.word_length
        else:
            self.config = EventConfig.from_dict(config)
            self.config.word_length = self.word_length

        self.is_manager_backed = False
        self.channel_names: list[str] = []
        self.channel_experiments: dict[str, EventExperiment] = {}
        self.pairwise_mutual_information: dict[tuple[str, str], float] = {}
        self.output_path: Path | None = None
        if manager is not None:
            self._initialize_from_manager(manager)
        elif sequence is not None:
            self.sequence = tuple(np.asarray(sequence).ravel().tolist())
            self.recording_sequences = (self.sequence,)
        else:
            raise ValueError("No sequence or manager found")

        self.counts: dict[tuple[Any, ...], int] = {}
        self.marginal_counts: dict[tuple[Any, ...], int] = {}
        self.total_count = 0
        self.word_distribution: WordDistribution = WordDistribution()
        self.block_entropy = 0.0
        self.block_entropy_plus_one = 0.0
        self._entropy_conditional = 0.0
        self._build_counts_and_distributions()
        if manager is not None:
            self.output_path = self._manager_output_path(manager)
            self.save(self.output_path)

    def _initialize_from_manager(self, manager: DataManager) -> None:
        simple_event = self.config.simple_event
        if simple_event.window < SAMPLE_INTERVAL:
            raise ValueError(f"simple_event.window must be >= {SAMPLE_INTERVAL} ms.")
        if simple_event.stride < 1:
            raise ValueError("simple_event.stride must be >= 1.")
        if simple_event.discretization != "spike_detection":
            raise ValueError("DataManager supports only spike_detection simple-event discretization.")

        self.channel_names = list(manager.channel_names)
        if not self.channel_names or len(set(self.channel_names)) != len(self.channel_names):
            raise ValueError("DataManager channel_names must be non-empty and unique.")
        if self.config.source_name is None:
            self.config.source_name = str(manager.filename)

        threshold: float | pd.Series | None = None
        if simple_event.thresholds:
            if len(simple_event.thresholds) == 1:
                threshold = simple_event.thresholds[0]
            elif len(simple_event.thresholds) == len(self.channel_names):
                threshold = pd.Series(dict(zip(self.channel_names, simple_event.thresholds)))
            else:
                raise ValueError(
                    "simple_event.thresholds must contain one value or one value per channel."
                )
        elif getattr(manager, "global_mean_max", None) is not None:
            global_thresholds = manager.global_mean_max
            if isinstance(global_thresholds, pd.Series):
                threshold = global_thresholds.reindex(self.channel_names)
            elif isinstance(global_thresholds, Mapping):
                threshold = pd.Series(global_thresholds).reindex(self.channel_names)
            else:
                threshold = pd.Series(
                    {channel_name: float(global_thresholds) for channel_name in self.channel_names}
                )
            self.config.simple_event.thresholds = tuple(
                float(value) for value in threshold.reindex(self.channel_names).tolist()
            )

        events = np.asarray(
            manager.discretize(
                event_time_window=int(round(simple_event.window / SAMPLE_INTERVAL)),
                acceptance_threshold=threshold,
            )
        )
        if events.ndim != 3 or events.shape[2] != len(self.channel_names):
            raise ValueError(
                "DataManager.discretize() must return an array shaped "
                "(recordings, chunks, channels)."
            )
        events = events[:, :: simple_event.stride, :]

        self.is_manager_backed = True
        self.manager_recordings = events
        self.recording_sequences = tuple(
            tuple(tuple(row) for row in recording.tolist()) for recording in events
        )
        self.sequence = tuple(row for recording in self.recording_sequences for row in recording)

        for index, channel_name in enumerate(self.channel_names):
            channel_recordings = tuple(
                tuple(recording[:, index].tolist()) for recording in events
            )
            channel_experiment = EventExperiment(
                sequence=(),
                word_length=self.word_length,
                config=self.config,
            )
            channel_experiment.recording_sequences = channel_recordings
            channel_experiment.sequence = tuple(
                value for recording in channel_recordings for value in recording
            )
            channel_experiment.channel_name = channel_name
            channel_experiment.invalidate_cache()
            self.channel_experiments[channel_name] = channel_experiment

        for left, right in combinations(range(len(self.channel_names)), 2):
            pair = (self.channel_names[left], self.channel_names[right])
            self.pairwise_mutual_information[pair] = _pairwise_context_mutual_information(
                events, left, right, self.word_length
            )

    def _manager_output_path(self, manager: DataManager) -> Path:
        directory = Path(manager.data_dir) / "experiments"
        source_stem = Path(manager.filename).stem
        stem = f"{source_stem}_window-{self.config.simple_event.window:g}_word-{self.word_length}"
        target = directory / f"{stem}.json"
        suffix = 1
        while target.exists():
            target = directory / f"{stem}_({suffix}).json"
            suffix += 1
        return target

    @classmethod
    def load(cls, path: str | Path) -> "EventExperiment":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        config = EventConfig.from_dict(payload.get("config", {}))
        if payload.get("kind") == "manager":
            experiment = cls(
                sequence=[],
                word_length=config.word_length,
                config=config,
            )
            experiment.channel_names = list(payload["channel_names"])
            experiment._restore_manager_recordings(payload["recordings"])
            return experiment
        sequence = payload.get("sequence", [])
        return cls(sequence, word_length=config.word_length, config=config)

    def _restore_manager_recordings(self, recordings: Sequence[Any]) -> None:
        events = np.asarray(recordings)
        if events.ndim != 3 or events.shape[2] != len(self.channel_names):
            raise ValueError("Saved manager experiment has invalid recording dimensions.")
        self.is_manager_backed = True
        self.manager_recordings = events
        self.recording_sequences = tuple(
            tuple(tuple(row) for row in recording.tolist()) for recording in events
        )
        self.sequence = tuple(row for recording in self.recording_sequences for row in recording)
        self.channel_experiments = {}
        for index, channel_name in enumerate(self.channel_names):
            channel_recordings = tuple(tuple(recording[:, index].tolist()) for recording in events)
            channel_experiment = EventExperiment(
                sequence=(),
                word_length=self.word_length,
                config=self.config,
            )
            channel_experiment.recording_sequences = channel_recordings
            channel_experiment.sequence = tuple(
                value for recording in channel_recordings for value in recording
            )
            channel_experiment.channel_name = channel_name
            channel_experiment.invalidate_cache()
            self.channel_experiments[channel_name] = channel_experiment
        self.pairwise_mutual_information = {}
        for left, right in combinations(range(len(self.channel_names)), 2):
            pair = (self.channel_names[left], self.channel_names[right])
            self.pairwise_mutual_information[pair] = _pairwise_context_mutual_information(
                events, left, right, self.word_length
            )
        self.invalidate_cache()

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if self.is_manager_backed:
            payload = {
                "version": 2,
                "kind": "manager",
                "config": self.config.to_dict(),
                "channel_names": self.channel_names,
                "recordings": self.manager_recordings.tolist(),
                "counts": self._serialize_counts(self.counts),
                "metrics": self._metrics_payload(self),
                "channel_metrics": {
                    name: self._metrics_payload(experiment)
                    for name, experiment in self.channel_experiments.items()
                },
                "pairwise_mutual_information": [
                    {"channels": list(pair), "bits": value}
                    for pair, value in self.pairwise_mutual_information.items()
                ],
            }
        else:
            payload = {
                "version": 1,
                "config": self.config.to_dict(),
                "sequence": list(self.sequence),
                "counts": self._serialize_counts(self.counts),
                "metrics": self._metrics_payload(self),
            }

        with target.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                default=str,
                sort_keys=True,
                indent=2
            )
        return target

    @staticmethod
    def _metrics_payload(experiment: "EventExperiment") -> dict[str, float]:
        return {
            "block_entropy": experiment.block_entropy,
            "block_entropy_plus_one": experiment.block_entropy_plus_one,
            "entropy_conditional": experiment.entropy_conditional,
            "total_count": experiment.total_count,
        }

    @staticmethod
    def _serialize_counts(counts: Mapping[tuple[Any, ...], int]) -> dict[str, int]:
        return {",".join(str(part) for part in key): value for key, value in counts.items()}

    def invalidate_cache(self) -> None:
        self._build_counts_and_distributions()

    def _build_counts_and_distributions(self) -> None:
        self.counts = Counter()
        self.marginal_counts = {}
        sequences = getattr(self, "recording_sequences", (self.sequence,))
        if not any(sequences):
            self.word_distribution = WordDistribution()
            self.block_entropy = 0.0
            self.block_entropy_plus_one = 0.0
            self._entropy_conditional = 0.0
            self.total_count = 0
            return

        full_length = self.word_length + 1
        for sequence in sequences:
            windows = zip(
                *(islice(sequence, offset, None) for offset in range(full_length))
            )
            self.counts.update(windows)

        self.total_count = float(sum(self.counts.values()))
        if self.total_count == 0:
            self.word_distribution = WordDistribution()
            self.block_entropy = 0.0
            self.block_entropy_plus_one = 0.0
            self._entropy_conditional = 0.0
            return

        for key, value in self.counts.items():
            prefix = key[:-1]
            self.marginal_counts[prefix] = self.marginal_counts.get(prefix, 0) + value
        self.word_distribution = WordDistribution(
            {key: value / self.total_count for key, value in self.marginal_counts.items()}
        )

        self.block_entropy = self._entropy_from_distribution(self.word_distribution)
        self.block_entropy_plus_one = self._entropy_from_counts(self.counts)
        self._entropy_conditional = self.block_entropy_plus_one - self.block_entropy

    @staticmethod
    def _entropy_from_distribution(distribution: Mapping[tuple[Any, ...], float]) -> float:
        total = 0.0
        for prob in distribution.values():
            if prob > 0:
                total -= prob * math.log2(prob)
        return total

    @staticmethod
    def _entropy_from_counts(counts: Mapping[tuple[Any, ...], int]) -> float:
        total = sum(counts.values())
        if total == 0:
            return 0.0
        entropy = 0.0
        for count in counts.values():
            prob = count / total
            if prob > 0:
                entropy -= prob * math.log2(prob)
        return entropy

    @staticmethod
    def _normalize_word(word: Any) -> tuple[Any, ...]:
        if word is None:
            return ()
        if isinstance(word, tuple):
            return word
        if isinstance(word, list):
            return tuple(word)
        if isinstance(word, np.ndarray):
            return tuple(word.tolist())
        return (word,)

    @staticmethod
    def _normalize_sequence(sequence: Any) -> tuple[Any, ...]:
        if sequence is None:
            return ()
        if isinstance(sequence, (list, tuple, np.ndarray)):
            return tuple(np.asarray(sequence).ravel().tolist())
        return (sequence,)

    def _marginal_prob(self, sequence: Sequence[Any]) -> float:
        seq = self._normalize_sequence(sequence)
        if len(seq) == 0:
            return 1.0

        if len(seq) >= self.word_length:
            return float(self.word_distribution.get(seq[: self.word_length], 0.0))

        total = 0.0
        for full_word, count in self.counts.items():
            if full_word[: len(seq)] == seq:
                total += count
        return 0.0 if self.total_count == 0 else total / self.total_count

    def prob(self, *args: Any) -> float:
        if len(args) == 1:
            word = args[0]
        elif len(args) == 2:
            _, word = args
        else:
            raise TypeError("prob expects (word) or (channel, word)")

        word = self._normalize_word(word)
        if not word:
            return 1.0

        if len(word) < self.word_length:
            return self._marginal_prob(word)
        if len(word) == self.word_length:
            return float(self.word_distribution.get(word, 0.0))
        return self.chain_prob(word)

    def cond_prob(self, *args: Any) -> float:
        if len(args) == 2:
            symbol, word = args
        elif len(args) == 3:
            _, symbol, word = args
        else:
            raise TypeError("cond_prob expects (s, word) or (channel, s, word)")

        symbol = self._normalize_word(symbol)
        if not symbol:
            return 0.0
        symbol = (symbol[0],)

        context = self._normalize_word(word)
        if len(context) > self.word_length:
            context = context[-self.word_length :]

        if len(context) < self.word_length:
            numerator = 0.0
            denominator = 0.0
            for full_word, count in self.counts.items():
                if len(full_word) < len(context) + 1:
                    continue
                if full_word[: len(context)] == context:
                    denominator += count
                    if full_word[len(context)] == symbol[0]:
                        numerator += count
            return 0.0 if denominator == 0 else numerator / denominator

        numerator = 0.0
        denominator = self.marginal_counts.get(context, 0)
        for full_word, count in self.counts.items():
            if full_word[:-1] == context and full_word[-1] == symbol[0]:
                numerator += count
        return 0.0 if denominator == 0 else numerator / denominator

    def chain_prob(self, *args: Any) -> float:
        if len(args) == 1:
            sequence = args[0]
        elif len(args) == 2:
            _, sequence = args
        else:
            raise TypeError("chain_prob expects (sequence) or (channel, sequence)")

        seq = self._normalize_sequence(sequence)
        if not seq:
            return 1.0

        if len(seq) < self.word_length:
            return self._marginal_prob(seq)
        if len(seq) == self.word_length:
            return float(self.word_distribution.get(seq, 0.0))

        probability = self.prob(seq[: self.word_length])
        if probability == 0:
            return 0.0

        for index in range(self.word_length, len(seq)):
            context = seq[index - self.word_length : index]
            next_symbol = seq[index]
            conditional = self.cond_prob(next_symbol, context)
            if conditional == 0.0:
                return 0.0
            probability *= conditional
        return probability

    @property
    def entropy_block(self) -> float:
        return self.block_entropy

    @property
    def entropy_plus_one(self) -> float:
        return self.block_entropy_plus_one

    @property
    def entropy_conditional(self) -> float:
        return self._entropy_conditional

    @property
    def transition_states(self) -> tuple[tuple[Any, ...], ...]:
        if self.word_length == 0:
            return ()
        states = {
            state
            for word in self.counts
            for state in (word[:-1], word[1:])
        }
        return tuple(sorted(states, key=lambda x: tuple(str(v) for v in x)))

    @property
    def transition_matrix(self) -> np.ndarray:
        states = self.transition_states
        if not states:
            return np.zeros((0, 0), dtype=float)

        state_index = {state: i for i, state in enumerate(states)}
        trans = np.zeros((len(states), len(states)), dtype=float)
        row_totals = np.zeros(len(states), dtype=float)
        for word, count in self.counts.items():
            source = state_index[word[:-1]]
            target = state_index[word[1:]]
            trans[source, target] += count
            row_totals[source] += count
        nonzero_rows = row_totals > 0
        trans[nonzero_rows] /= row_totals[nonzero_rows, np.newaxis]
        return trans

    def __repr__(self) -> str:
        return (
            f"EventExperiment(word_length={self.word_length}, "
            f"n_words={len(self.counts)}, total_count={self.total_count})"
        )


def information_event(sequence: Sequence[Any] | np.ndarray, word_length: int = 1, config=None) -> EventExperiment:
    return EventExperiment(sequence, word_length=word_length, config=config)


class InformationEvent(EventExperiment):
    pass


def mutual_information(x: Sequence[Any] | np.ndarray, y: Sequence[Any] | np.ndarray) -> float:
    """Compute I(X; Y) in bits using the aligned joint distribution."""

    x_array = np.asarray(x).ravel()
    y_array = np.asarray(y).ravel()
    if x_array.shape[0] != y_array.shape[0]:
        raise ValueError("x and y must have the same length.")
    if x_array.size == 0:
        return 0.0

    x_values = np.unique(x_array)
    y_values = np.unique(y_array)
    joint = np.zeros((x_values.size, y_values.size), dtype=float)
    for xi, yi in zip(x_array, y_array):
        i = int(np.where(x_values == xi)[0][0])
        j = int(np.where(y_values == yi)[0][0])
        joint[i, j] += 1.0
    joint /= joint.sum()

    px = joint.sum(axis=1)
    py = joint.sum(axis=0)
    mi = 0.0
    for i, p_x in enumerate(px):
        for j, p_y in enumerate(py):
            p_xy = joint[i, j]
            if p_xy > 0.0:
                mi += p_xy * math.log2(p_xy / (p_x * p_y))
    return float(mi)


def _pairwise_context_mutual_information(
    events: np.ndarray,
    left_channel: int,
    right_channel: int,
    context_length: int,
) -> float:
    left_categories: dict[tuple[Any, ...], int] = {}
    right_categories: dict[tuple[Any, ...], int] = {}
    left_codes: list[int] = []
    right_codes: list[int] = []

    for recording in events:
        context_count = max(0, recording.shape[0] - context_length)
        for start in range(context_count):
            left_context = tuple(
                recording[start : start + context_length, left_channel].tolist()
            )
            right_context = tuple(
                recording[start : start + context_length, right_channel].tolist()
            )
            if left_context not in left_categories:
                left_categories[left_context] = len(left_categories)
            if right_context not in right_categories:
                right_categories[right_context] = len(right_categories)
            left_codes.append(left_categories[left_context])
            right_codes.append(right_categories[right_context])

    return mutual_information(left_codes, right_codes)


__all__ = [
    "EventConfig",
    "EventExperiment",
    "InformationEvent",
    "SimpleEventConfig",
    "WordDistribution",
    "information_event",
    "mutual_information",
]
