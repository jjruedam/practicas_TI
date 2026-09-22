from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from DataManager import DataManager

class WordDistribution(dict):
    """Dictionary-like distribution whose values() returns a NumPy array."""

    def values(self):
        return np.asarray(list(super().values()), dtype=float)


@dataclass
class SimpleEventConfig:
    """Serializable configuration for the simple-event extraction stage."""

    name: str = "simple_event"
    window: int = 1
    stride: int = 1
    discretization: str = "identity"
    thresholds: tuple[float, ...] = ()
    bins: tuple[float, ...] | None = None
    labels: tuple[Any, ...] | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window": self.window,
            "stride": self.stride,
            "discretization": self.discretization,
            "thresholds": list(self.thresholds),
            "bins": list(self.bins) if self.bins is not None else None,
            "labels": list(self.labels) if self.labels is not None else None,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SimpleEventConfig":
        return cls(
            name=str(data.get("name", "simple_event")),
            window=int(data.get("window", 1)),
            stride=int(data.get("stride", 1)),
            discretization=str(data.get("discretization", "identity")),
            thresholds=tuple(float(v) for v in data.get("thresholds", ())),
            bins=None if data.get("bins") is None else tuple(float(v) for v in data["bins"]),
            labels=None if data.get("labels") is None else tuple(data["labels"]),
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
        word_length: int = 1,
        config: EventConfig | Mapping[str, Any] | None = None,
    ):
        if manager is not None:
            channels = manager.channel_names
            self.sequence = manager.discretize(event_time_window=40)[:,0]
        elif sequence is not None:
            self.sequence = tuple(np.asarray(sequence).ravel().tolist())
        else:
            raise ValueError("No sequence or manager found")
        self.word_length = int(word_length)
        if self.word_length < 1:
            raise ValueError("word_length must be >= 1.")

        if config is None:
            self.config = EventConfig(word_length=self.word_length)
        elif isinstance(config, EventConfig):
            self.config = config
            if self.config.word_length != self.word_length:
                self.config.word_length = self.word_length
        else:
            self.config = EventConfig.from_dict(config)
            self.config.word_length = self.word_length

        self.counts: dict[tuple[Any, ...], int] = {}
        self.marginal_counts: dict[tuple[Any, ...], int] = {}
        self.total_count = 0
        self.word_distribution: WordDistribution = WordDistribution()
        self.block_entropy = 0.0
        self.block_entropy_plus_one = 0.0
        self._entropy_conditional = 0.0
        self._build_counts_and_distributions()

    @classmethod
    def load(cls, path: str | Path) -> "EventExperiment":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        config = EventConfig.from_dict(payload.get("config", {}))
        sequence = payload.get("sequence", [])
        return cls(sequence, word_length=config.word_length, config=config)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": 1,
            "config": self.config.to_dict(),
            "sequence": list(self.sequence),
            "counts": self._serialize_counts(self.counts),
            "metrics": {
                "block_entropy": self.block_entropy,
                "block_entropy_plus_one": self.block_entropy_plus_one,
                "entropy_conditional": self.entropy_conditional,
                "total_count": self.total_count,
            },
        }

        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str, sort_keys=True)
        return target

    @staticmethod
    def _serialize_counts(counts: Mapping[tuple[Any, ...], int]) -> dict[str, int]:
        return {",".join(str(part) for part in key): value for key, value in counts.items()}

    def invalidate_cache(self) -> None:
        self._build_counts_and_distributions()

    def _build_counts_and_distributions(self) -> None:
        self.counts = {}
        self.marginal_counts = {}
        if not self.sequence:
            self.word_distribution = WordDistribution()
            self.block_entropy = 0.0
            self.block_entropy_plus_one = 0.0
            self._entropy_conditional = 0.0
            self.total_count = 0
            return

        full_length = self.word_length + 1
        for i in range(len(self.sequence) - full_length + 1):
            word = tuple(self.sequence[i : i + full_length])
            self.counts[word] = self.counts.get(word, 0) + 1

        self.total_count = float(sum(self.counts.values()))
        if self.total_count == 0:
            self.word_distribution = WordDistribution()
            self.block_entropy = 0.0
            self.block_entropy_plus_one = 0.0
            self._entropy_conditional = 0.0
            return

        if self.word_length == 1:
            symbol_counts: dict[Any, int] = {}
            for event in self.sequence:
                symbol_counts[event] = symbol_counts.get(event, 0) + 1
            self.marginal_counts = {k: v for k, v in symbol_counts.items()}
            total_symbols = float(len(self.sequence))
            self.word_distribution = WordDistribution(
                {key: value / total_symbols for key, value in symbol_counts.items()}
            )
        else:
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
    def transition_matrix(self) -> np.ndarray:
        if self.word_length == 0:
            return np.asarray([[]], dtype=float)

        states = sorted(self.word_distribution.keys(), key=lambda x: tuple(str(v) for v in x))
        if not states:
            return np.zeros((0, 0), dtype=float)

        state_index = {state: i for i, state in enumerate(states)}
        trans = np.zeros((len(states), len(states)), dtype=float)
        for state in states:
            row_total = 0.0
            for symbol in sorted(set(v for word in self.counts for v in word), key=lambda x: str(x)):
                next_state = state[1:] + (symbol,)
                if next_state not in state_index:
                    continue
                count = 0
                for full_word, c in self.counts.items():
                    if full_word[:-1] == state and full_word[-1] == symbol:
                        count += c
                trans[state_index[state], state_index[next_state]] += count
                row_total += count
            if row_total > 0:
                trans[state_index[state]] /= row_total
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


__all__ = [
    "EventConfig",
    "EventExperiment",
    "InformationEvent",
    "SimpleEventConfig",
    "WordDistribution",
    "information_event",
    "mutual_information",
]
