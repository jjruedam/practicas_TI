"""Build and persist information statistics for discrete event sequences.

Sequences can be supplied directly or derived from a :class:`DataManager` by
detecting spikes in fixed time windows. The resulting experiment counts
overlapping words, computes their block entropy, and can compare channels
using mutual information.
"""

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
import matplotlib.pyplot as plt

from DataManager import DataManager, SAMPLE_INTERVAL

class WordDistribution(dict):
    """Dictionary-like probability distribution with array-valued ``values()``."""

    def values(self):
        """Return distribution probabilities as a NumPy array."""
        return np.asarray(list(super().values()), dtype=float)


@dataclass
class SimpleEventConfig:
    """Configuration for converting continuous signals into simple events.

    Parameters
    ----------
    name : str
        Identifier for the simple-event configuration.
    window : float
        Event window in milliseconds.
    stride : int
        Number of discretized windows to skip between retained events.
    discretization : str
        Discretization method. ``DataManager`` experiments support
        ``"spike_detection"``.
    thresholds : tuple of float
        Optional spike-detection threshold, either a single value shared by
        all channels or one value per channel.
    version : int
        Configuration format version.
    """

    name: str = "simple_event"
    window: float = 1
    stride: int = 1
    discretization: str = "spike_detection"
    thresholds: tuple[float, ...] = ()
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this configuration."""
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
        """Create a configuration from a mapping, applying field defaults."""
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
    """Configuration for an event experiment.

    Parameters
    ----------
    word_length : int
        Number of consecutive simple events in each counted word.
    simple_event : SimpleEventConfig
        Settings used to derive simple events from continuous data.
    source_name : str or None
        Optional name of the source data file.
    version : int
        Configuration format version.
    notes : str
        Optional free-form experiment notes.
    """

    word_length: int = 1
    simple_event: SimpleEventConfig = field(default_factory=SimpleEventConfig)
    source_name: str | None = None
    version: int = 1
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this configuration."""
        return {
            "word_length": self.word_length,
            "simple_event": self.simple_event.to_dict(),
            "source_name": self.source_name,
            "version": self.version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventConfig":
        """Create a configuration from a mapping, applying field defaults."""
        simple_event_data = data.get("simple_event", {})
        return cls(
            word_length=int(data.get("word_length", 1)),
            simple_event=SimpleEventConfig.from_dict(simple_event_data),
            source_name=data.get("source_name"),
            version=int(data.get("version", 1)),
            notes=str(data.get("notes", "")),
        )


class EventExperiment:
    """Compute word statistics and information measures for event sequences.

    An experiment can be initialized from one sequence or from a
    :class:`DataManager`. In the latter case, each channel gets its own
    experiment and pairwise mutual information is computed between channels.
    Words are formed from overlapping windows of ``word_length`` events;
    separate recordings are counted independently.

    Parameters
    ----------
    sequence : sequence or np.ndarray, optional
        One-dimensional event sequence. Ignored when ``manager`` is supplied.
    manager : DataManager, optional
        Source of continuous recordings to discretize and analyze.
    word_length : int, optional
        Number of events per word. Defaults to the value in ``config`` or 1.
    config : EventConfig or mapping, optional
        Experiment and simple-event settings. A mapping is converted to an
        :class:`EventConfig`.

    Raises
    ------
    ValueError
        If ``word_length`` is less than one, neither input source is supplied,
        or the manager's simple-event settings or data are invalid.
    """

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
        self.pairwise_normalized_mutual_information: dict[tuple[str, str], tuple[float, float]] = {}
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
        self._build_counts_and_distributions()
        if manager is not None:
            self.output_path = self._manager_output_path(manager)
            self.save(self.output_path)

    def _initialize_from_manager(self, manager: DataManager) -> None:
        """Discretize manager recordings and calculate per-channel metrics.

        The simple-event settings determine the discretization window,
        stride, and thresholds. Recording boundaries are preserved when
        building sequences and when calculating pairwise context information.
        """
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

        window_samples = int(round(simple_event.window / SAMPLE_INTERVAL))
        events_by_recording = [
            np.asarray(
                manager.discretize_recording(
                    index,
                    event_time_window=window_samples,
                    acceptance_threshold=threshold,
                ),
                dtype=np.uint8,
            )
            for index in range(manager.n_recordings)
        ]
        events = np.stack(events_by_recording)
        if events.ndim != 3 or events.shape[2] != len(self.channel_names):
            raise ValueError(
                "DataManager.discretize_recording() must return an array shaped "
                "(recordings, chunks, channels)."
            )
        events = events[:, :: simple_event.stride, :]

        self.is_manager_backed = True
        self.manager_recordings = events
        self.recording_sequences = ()
        self.sequence = ()

        for index, channel_name in enumerate(self.channel_names):
            channel_experiment = EventExperiment(
                sequence=(),
                word_length=self.word_length,
                config=self.config,
            )
            channel_experiment.channel_name = channel_name
            channel_experiment._set_count_statistics(
                self._binary_recording_word_counts(
                    events,
                    index,
                    self.word_length,
                )
            )
            self.channel_experiments[channel_name] = channel_experiment

        for left, right in combinations(range(len(self.channel_names)), 2):
            ch_left = self.channel_names[left]
            ch_right = self.channel_names[right]
            pair = (ch_left, ch_right)

            mi = _pairwise_context_mutual_information(
                events, left, right, self.word_length
            )
            self.pairwise_mutual_information[pair] = mi

            # Entropías de cada canal
            h_left = self.channel_experiments[ch_left].block_entropy
            h_right = self.channel_experiments[ch_right].block_entropy

            # Guardar un diccionario explícito por nombre de canal en vez de una tupla por posiciones
            self.pairwise_normalized_mutual_information[pair] = {
                ch_left: float(mi / h_left) if h_left > 0 else 0.0,
                ch_right: float(mi / h_right) if h_right > 0 else 0.0,
            }

    @staticmethod
    def _binary_recording_word_counts(
        recordings: np.ndarray,
        channel_index: int,
        word_length: int,
    ) -> Counter[tuple[int, ...]]:
        """Count binary words without materializing every event as a Python object."""
        counts: Counter[tuple[int, ...]] = Counter()
        if word_length > 64:
            for recording in recordings:
                values = recording[:, channel_index]
                windows = zip(
                    *(islice(values, offset, None) for offset in range(word_length))
                )
                counts.update(windows)
            return counts

        for recording in recordings:
            context_count = recording.shape[0] - word_length + 1
            if context_count <= 0:
                continue
            codes = np.zeros(context_count, dtype=np.uint64)
            values = recording[:, channel_index]
            for offset in range(word_length):
                codes = (codes << 1) | values[offset : offset + context_count]
            unique_codes, frequencies = np.unique(codes, return_counts=True)
            for code, frequency in zip(unique_codes, frequencies):
                word = tuple(
                    int((code >> bit) & 1)
                    for bit in range(word_length - 1, -1, -1)
                )
                counts[word] += int(frequency)
        return counts

    @staticmethod
    def _multichannel_recording_word_counts(
        recordings: np.ndarray,
        word_length: int,
    ) -> Counter[tuple[tuple[int, ...], ...]]:
        """Count joint channel words while preserving recording boundaries."""
        counts: Counter[tuple[tuple[int, ...], ...]] = Counter()
        channel_count = recordings.shape[2]
        bits_per_word = word_length * channel_count
        if bits_per_word > 64:
            for recording in recordings:
                context_count = recording.shape[0] - word_length + 1
                counts.update(
                    tuple(
                        tuple(int(value) for value in row)
                        for row in recording[start : start + word_length].tolist()
                    )
                    for start in range(max(0, context_count))
                )
            return counts

        for recording in recordings:
            context_count = recording.shape[0] - word_length + 1
            if context_count <= 0:
                continue
            row_codes = np.zeros(recording.shape[0], dtype=np.uint64)
            for channel_index in range(channel_count):
                row_codes = (row_codes << 1) | recording[:, channel_index]
            word_codes = np.zeros(context_count, dtype=np.uint64)
            for offset in range(word_length):
                word_codes = (
                    (word_codes << channel_count)
                    | row_codes[offset : offset + context_count]
                )
            unique_codes, frequencies = np.unique(word_codes, return_counts=True)
            row_mask = (1 << channel_count) - 1
            for code, frequency in zip(unique_codes, frequencies):
                word = []
                for offset in range(word_length - 1, -1, -1):
                    row_code = int((code >> (offset * channel_count)) & row_mask)
                    word.append(
                        tuple(
                            (row_code >> bit) & 1
                            for bit in range(channel_count - 1, -1, -1)
                        )
                    )
                counts[tuple(word)] += int(frequency)
        return counts

    def _manager_output_path(self, manager: DataManager) -> Path:
        """Choose a non-existing JSON path under the manager's experiments folder."""
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
        """Load an experiment's sequence and configuration from a JSON file.

        Parameters
        ----------
        path : str or Path
            JSON file previously written by :meth:`save`.

        Returns
        -------
        EventExperiment
            Experiment initialized with the serialized configuration and,
            for sequence-backed files, the serialized event sequence.
        """
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
            return experiment
        sequence = payload.get("sequence", [])
        return cls(sequence, word_length=config.word_length, config=config)

    def save(self, path: str | Path) -> Path:
        """Serialize experiment configuration, metrics, and counts as JSON.

        Manager-backed experiments also include channel metrics and pairwise
        mutual information. The target directory is created if necessary.

        Parameters
        ----------
        path : str or Path
            Destination JSON file.

        Returns
        -------
        Path
            The destination path.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if self.is_manager_backed:
            joint_entropy = [
                {
                    "channels": list(pair),
                    "bits": max(
                        0.0,
                        self.channel_experiments[pair[0]].block_entropy
                        + self.channel_experiments[pair[1]].block_entropy
                        - mutual_information,
                    ),
                }
                for pair, mutual_information in self.pairwise_mutual_information.items()
            ]
            if len(self.channel_names) == 1:
                channel_name = self.channel_names[0]
                joint_entropy.append(
                    {
                        "channels": [channel_name],
                        "bits": self.channel_experiments[channel_name].block_entropy,
                    }
                )
            payload = {
                "metrics": {
                    **self._metrics_payload(self),
                    "channel_names": self.channel_names,
                },
                "channel_entropy": {
                    name: experiment.block_entropy
                    for name, experiment in self.channel_experiments.items()
                },
                "joint_entropy": joint_entropy,
                "pairwise_mutual_information": [
                    {"channels": list(pair), "bits": value}
                    for pair, value in self.pairwise_mutual_information.items()
                ],
                "pairwise_normalized_mutual_information": [
                    {
                        "channels": list(pair),
                        f"nmi_normalized_by_{ch_left}": nmi_dict[ch_left],
                        f"nmi_normalized_by_{ch_right}": nmi_dict[ch_right],
                    }
                    for pair, nmi_dict in self.pairwise_normalized_mutual_information.items()
                    for ch_left, ch_right in [pair]
                ],
                "kind": "manager",
                "config": self.config.to_dict(),
                "counts": self._serialize_counts(self.counts),
                "version": 3,
            }
        else:
            payload = {
                "metrics": self._metrics_payload(self),
                "config": self.config.to_dict(),
                "sequence": list(self.sequence),
                "counts": self._serialize_counts(self.counts),
                "version": 2,
            }

        with target.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                default=str,
                sort_keys=False,
                indent=2
            )
        return target

    @staticmethod
    def _metrics_payload(experiment: "EventExperiment") -> dict[str, float]:
        """Return the scalar metrics stored for one experiment."""
        return {
            "block_entropy": experiment.block_entropy,
            "total_count": experiment.total_count,
        }

    @staticmethod
    def _serialize_counts(counts: Mapping[tuple[Any, ...], int]) -> dict[str, int]:
        """Convert tuple word keys to comma-separated JSON object keys."""
        return {",".join(str(part) for part in key): value for key, value in counts.items()}

    def invalidate_cache(self) -> None:
        """Rebuild word counts, marginal distributions, and block entropy."""
        self._build_counts_and_distributions()

    def _build_counts_and_distributions(self) -> None:
        """Count overlapping words independently within each recording."""
        if self.is_manager_backed:
            self._set_count_statistics(
                self._multichannel_recording_word_counts(
                    self.manager_recordings,
                    self.word_length,
                )
            )
            return

        counts: Counter[tuple[Any, ...]] = Counter()
        sequences = getattr(self, "recording_sequences", (self.sequence,))
        if not any(sequences):
            self._set_count_statistics(counts)
            return

        full_length = self.word_length
        for sequence in sequences:
            windows = zip(
                *(islice(sequence, offset, None) for offset in range(full_length))
            )
            counts.update(windows)

        self._set_count_statistics(counts)

    def _set_count_statistics(
        self,
        counts: Mapping[tuple[Any, ...], int],
    ) -> None:
        """Set word counts and their derived marginal and entropy statistics."""
        self.counts = Counter(counts)
        self.marginal_counts = {}
        self.total_count = float(sum(self.counts.values()))
        if self.total_count == 0:
            self.word_distribution = WordDistribution()
            self.block_entropy = 0.0
            return

        for key, value in self.counts.items():
            prefix = key[:-1]
            self.marginal_counts[prefix] = self.marginal_counts.get(prefix, 0) + value
        self.word_distribution = WordDistribution(
            {key: value / self.total_count for key, value in self.counts.items()}
        )
        self.block_entropy = self._entropy_from_counts(self.counts)

    @staticmethod
    def _entropy_from_counts(counts: Mapping[tuple[Any, ...], int]) -> float:
        """Compute Shannon entropy in bits from word counts."""
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
        """Convert a scalar or array-like word to a tuple."""
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
        """Convert a scalar or array-like sequence to a flat tuple."""
        if sequence is None:
            return ()
        if isinstance(sequence, (list, tuple, np.ndarray)):
            return tuple(np.asarray(sequence).ravel().tolist())
        return (sequence,)

    def _marginal_prob(self, sequence: Sequence[Any]) -> float:
        """Return the empirical probability of a sequence prefix.

        Empty sequences have probability one. Prefixes shorter than
        ``word_length`` are summed over matching full words.
        """
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

    def __repr__(self) -> str:
        """Return a compact summary of the experiment's word counts."""
        return (
            f"EventExperiment(word_length={self.word_length}, "
            f"n_words={len(self.counts)}, total_count={self.total_count})"
        )


def _pairwise_context_mutual_information(
    events: np.ndarray,
    left_channel: int,
    right_channel: int,
    context_length: int,
) -> float:
    """Compute mutual information between channel contexts across recordings.

    Each observation is a tuple of ``context_length``, equivalent with word_length, it means
    consecutive events from one channel. Contexts are never formed across recording boundaries.
    ``left_channel`` and ``right_channel`` are column indexes into ``events``.
    """
    if context_length <= 32:
        joint_counts: Counter[int] = Counter()
        for recording in events:
            context_count = recording.shape[0] - context_length + 1
            if context_count <= 0:
                continue
            left_codes = np.zeros(context_count, dtype=np.uint64)
            right_codes = np.zeros(context_count, dtype=np.uint64)
            for offset in range(context_length):
                left_codes = (
                    (left_codes << 1)
                    | recording[offset : offset + context_count, left_channel]
                )
                right_codes = (
                    (right_codes << 1)
                    | recording[offset : offset + context_count, right_channel]
                )
            packed_codes = (left_codes << context_length) | right_codes
            unique_codes, frequencies = np.unique(
                packed_codes,
                return_counts=True,
            )
            joint_counts.update(
                {
                    int(code): int(frequency)
                    for code, frequency in zip(unique_codes, frequencies)
                }
            )

        left_counts: Counter[int] = Counter()
        right_counts: Counter[int] = Counter()
        right_mask = (1 << context_length) - 1
        for packed_code, count in joint_counts.items():
            left_counts[packed_code >> context_length] += count
            right_counts[packed_code & right_mask] += count
    else:
        joint_counts: Counter[tuple[tuple[Any, ...], tuple[Any, ...]]] = Counter()
        for recording in events:
            context_count = max(0, recording.shape[0] - context_length + 1)
            for start in range(context_count):
                left_context = tuple(
                    recording[start : start + context_length, left_channel].tolist()
                )
                right_context = tuple(
                    recording[start : start + context_length, right_channel].tolist()
                )
                joint_counts[(left_context, right_context)] += 1

        left_counts = Counter()
        right_counts = Counter()
        for (left_context, right_context), count in joint_counts.items():
            left_counts[left_context] += count
            right_counts[right_context] += count

    left_entropy = EventExperiment._entropy_from_counts(left_counts)
    right_entropy = EventExperiment._entropy_from_counts(right_counts)
    joint_entropy = EventExperiment._entropy_from_counts(joint_counts)
    return max(0.0, left_entropy + right_entropy - joint_entropy)


def plot_experiment_sweep(
    experiment_paths: Sequence[str | Path],
    state_name: str,
    admissible_interval: tuple[float, float] | None = None,
) -> None:
    """Plot mutual information and channel-normalized MI across sweep results.

    Each figure contains one panel per word length. Metrics and plotting
    labels are read from the EventExperiment JSON files.
    """
    rows = []
    for experiment_path in experiment_paths:
        with Path(experiment_path).open(encoding="utf-8") as file:
            payload = json.load(file)
        pair = payload["pairwise_mutual_information"][0]
        normalized = payload["pairwise_normalized_mutual_information"][0]
        rows.append(
            {
                "window_ms": float(payload["config"]["simple_event"]["window"]),
                "word_length": int(payload["config"]["word_length"]),
                "channels": pair["channels"],
                "mutual_information_bits": float(pair["bits"]),
                "normalized": {
                    key.removeprefix("nmi_normalized_by_"): float(value)
                    for key, value in normalized.items()
                    if key.startswith("nmi_normalized_by_")
                },
            }
        )
    if not rows:
        raise ValueError("At least one experiment JSON path is required.")

    channel_names = rows[0]["channels"]
    normalization_names = list(rows[0]["normalized"])
    if len(channel_names) != 2 or not normalization_names:
        raise ValueError(
            "Experiment JSON must contain pairwise MI and normalized MI for two channels."
        )
    if any(
        row["channels"] != channel_names
        or list(row["normalized"]) != normalization_names
        for row in rows
    ):
        raise ValueError("All experiment JSON files must use the same channel pair.")

    metrics = [
        ("mutual_information_bits", "Información mutua (bits)", "Información mutua"),
        *[
            (
                f"normalized:{stimulus_channel}",
                f"IM normalizada (bits / H({stimulus_channel}))",
                (
                    f"Transferencia de información: estímulo "
                    f"{stimulus_channel} "
                    f"→ respuesta "
                    f"{next(name for name in channel_names if name != stimulus_channel)}"
                ),
            )
            for stimulus_channel in normalization_names
        ],
    ]
    word_lengths = sorted({row["word_length"] for row in rows})
    for metric_name, ylabel, metric_title in metrics:
        fig, axes = plt.subplots(
            4,
            2,
            figsize=(12, 14),
            sharex=True,
            squeeze=False,
        )
        for word_length, ax in zip(word_lengths, axes.flat):
            word_rows = sorted(
                (row for row in rows if row["word_length"] == word_length),
                key=lambda row: row["window_ms"],
            )
            windows = [row["window_ms"] for row in word_rows]
            values = [
                row["mutual_information_bits"]
                if metric_name == "mutual_information_bits"
                else row["normalized"][metric_name.split(":", 1)[1]]
                for row in word_rows
            ]
            if admissible_interval is not None:
                lower, upper = admissible_interval
                ax.axvspan(lower, upper, color="tab:green", alpha=0.10)
                ax.axvline(lower, color="tab:green", linestyle="--", linewidth=0.8)
                ax.axvline(upper, color="tab:green", linestyle="--", linewidth=0.8)
            ax.plot(windows, values, marker="o", markersize=3)
            ax.set_title(f"Palabra N={word_length}")
            ax.set_xlabel("Ventana (ms)")
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
        for ax in axes.flat[len(word_lengths) :]:
            ax.set_visible(False)
        fig.suptitle(
            f"{state_name}: {metric_title} entre {channel_names[0]} y {channel_names[1]}"
        )
        fig.tight_layout()
        plt.show()
        plt.close(fig)


def plot_comparative_mutual_information(
    experiment_paths_by_state: Mapping[str, Sequence[str | Path]],
) -> None:
    """Plot the mutual-information sweeps for three states in one 4x2 figure."""
    if len(experiment_paths_by_state) != 3:
        raise ValueError("Exactly three states must be provided for comparison.")

    rows_by_state: dict[str, list[dict[str, Any]]] = {}
    channel_names: list[str] | None = None
    expected_word_lengths = set(range(1, 9))

    for state_name, experiment_paths in experiment_paths_by_state.items():
        rows: list[dict[str, Any]] = []
        seen_points: set[tuple[int, float]] = set()
        for experiment_path in experiment_paths:
            with Path(experiment_path).open(encoding="utf-8") as file:
                payload = json.load(file)
            pair = payload["pairwise_mutual_information"][0]
            channels = pair["channels"]
            if channel_names is None:
                channel_names = channels
            elif channels != channel_names:
                raise ValueError(
                    "All experiment JSON files must use the same channel pair."
                )

            window_ms = float(payload["config"]["simple_event"]["window"])
            word_length = int(payload["config"]["word_length"])
            point = (word_length, window_ms)
            if point in seen_points:
                raise ValueError(
                    f"Duplicate experiment for {state_name}, "
                    f"N={word_length}, window={window_ms:g} ms."
                )
            seen_points.add(point)
            rows.append(
                {
                    "window_ms": window_ms,
                    "word_length": word_length,
                    "mutual_information_bits": float(pair["bits"]),
                    "normalized": {
                        key.removeprefix("nmi_normalized_by_"): float(value)
                        for key, value in payload[
                            "pairwise_normalized_mutual_information"
                        ][0].items()
                        if key.startswith("nmi_normalized_by_")
                    },
                }
            )

        word_lengths = {row["word_length"] for row in rows}
        if word_lengths != expected_word_lengths:
            raise ValueError(
                f"State {state_name!r} must include word lengths 1 through 8."
            )
        rows_by_state[state_name] = rows

    if channel_names is None or len(channel_names) != 2:
        raise ValueError(
            "Experiment JSON must contain mutual information for two channels."
        )
    normalization_names = set(rows_by_state[next(iter(rows_by_state))][0]["normalized"])
    if normalization_names != set(channel_names):
        raise ValueError(
            "Experiment JSON must contain normalized MI for both channels."
        )
    if any(
        set(row["normalized"]) != normalization_names
        for rows in rows_by_state.values()
        for row in rows
    ):
        raise ValueError(
            "All experiment JSON files must contain normalized MI for both channels."
        )

    metrics = [
        (
            "mutual_information_bits",
            "Información mutua (bits)",
            (
                f"Comparación de la información mutua entre "
                f"{channel_names[0]} y {channel_names[1]}"
            ),
        ),
        *[
            (
                f"normalized:{stimulus_channel}",
                f"IM normalizada (I / H({stimulus_channel}))",
                (
                    f"IM normalizada: estímulo {stimulus_channel} "
                    f"→ respuesta "
                    f"{next(name for name in channel_names if name != stimulus_channel)}"
                ),
            )
            for stimulus_channel in channel_names
        ],
    ]
    colors = ("tab:blue", "tab:orange", "tab:green")
    for metric_name, ylabel, figure_title in metrics:
        fig, axes = plt.subplots(
            4,
            2,
            figsize=(12, 14),
            sharex=True,
            squeeze=False,
        )
        for word_length, ax in zip(range(1, 9), axes.flat):
            for (state_name, rows), color in zip(rows_by_state.items(), colors):
                word_rows = sorted(
                    (
                        row
                        for row in rows
                        if row["word_length"] == word_length
                    ),
                    key=lambda row: row["window_ms"],
                )
                values = [
                    row["mutual_information_bits"]
                    if metric_name == "mutual_information_bits"
                    else row["normalized"][metric_name.split(":", 1)[1]]
                    for row in word_rows
                ]
                ax.plot(
                    [row["window_ms"] for row in word_rows],
                    values,
                    marker="o",
                    markersize=3,
                    color=color,
                    label=state_name,
                )
            ax.set_title(f"Palabra N={word_length}")
            ax.set_xlabel("Ventana (ms)")
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)

        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.suptitle(figure_title, y=0.99)
        fig.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.955),
            ncol=3,
        )
        fig.tight_layout(rect=(0, 0, 1, 0.92))
        plt.show()
        plt.close(fig)


__all__ = [
    "EventConfig",
    "EventExperiment",
    "SimpleEventConfig",
    "WordDistribution",
    "plot_comparative_mutual_information",
    "plot_experiment_sweep",
]
