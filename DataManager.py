"""
DataManager: load and explore a single electric-potential data source.

Each data source is a text file containing a header (sample interval, number
of channels, number of samples) followed by 2-3 tab-separated, comparable
channels recorded continuously. The continuous stream is split into
fixed-length recordings (trials), since downstream analysis operates on
recordings rather than the raw continuous stream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).parent / "DatosSinapsisArtificial-TI"
RECORDING_LENGTH = 100000
SPIKE_LOOKAHEAD_OVERLAP = 2


class DataManager:
    """
    Loads and explores a single data source (2-3 comparable channels).

    Parameters
    ----------
    channel_names : sequence of str
        Names of the channels, in column order. Must match the channel count
        declared in the file header (2 or 3).
    filename : str
        Base name of the data file (with or without extension) inside `data_dir`.
    data_dir : str or Path, optional
        Folder containing the data file. Defaults to the practicas data folder.
    recording_length : int, optional
        Number of samples per recording/trial. Defaults to 100,000. Trailing
        samples that don't fill a full recording are dropped.
    """

    def __init__(
        self,
        channel_names: Sequence[str],
        filename: str,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        recording_length: int = RECORDING_LENGTH,
    ):
        self.channel_names = list(channel_names)
        self.filename = filename
        self.data_dir = Path(data_dir)
        self.recording_length = recording_length

        self.n_channels: int | None = None
        # recordings shape: (n_recordings, recording_length, n_channels)
        self.recordings: np.ndarray | None = None
        self.global_mean_max: float | None = None

        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load(self) -> None:
        filepath = self._resolve_filepath()
        n_channels = self._parse_header(filepath)

        if len(self.channel_names) != n_channels:
            raise ValueError(
                f"Got {len(self.channel_names)} channel names {self.channel_names}, "
                f"but the file header declares {n_channels} channels."
            )

        self.n_channels = n_channels

        # The file has a trailing delimiter, which produces one extra empty
        # column that we drop by keeping only the declared channel columns.
        raw = pd.read_csv(
            filepath,
            sep="\t",
            skiprows=3,
            decimal=",",
            header=None,
            usecols=range(n_channels),
            dtype=float,
        ).to_numpy()

        self.recordings = self._split_into_recordings(raw)
        self.global_mean_max= self._describe(None)["mean_max"]

    def _resolve_filepath(self) -> Path:
        candidate = self.data_dir / self.filename
        if candidate.exists():
            return candidate
        candidate_txt = self.data_dir / f"{self.filename}.txt"
        if candidate_txt.exists():
            return candidate_txt
        raise FileNotFoundError(f"Could not find data file for '{self.filename}' in {self.data_dir}")

    @staticmethod
    def _parse_header(filepath: Path) -> tuple[float, int]:
        """Read the 3-line header and return  n_channels."""
        with open(filepath, "r", encoding="utf-8") as f:
            header_lines = [next(f) for _ in range(3)]

        def _value(line: str) -> str:
            return line.split("=")[1].strip()
        
        n_channels = int(_value(header_lines[1]))
        return  n_channels

    def _split_into_recordings(self, raw: np.ndarray) -> np.ndarray:
        n_full = raw.shape[0] // self.recording_length
        if n_full == 0:
            raise ValueError(
                f"File has only {raw.shape[0]} samples, fewer than "
                f"recording_length={self.recording_length}."
            )
        usable = raw[: n_full * self.recording_length]
        return usable.reshape(n_full, self.recording_length, self.n_channels)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def n_recordings(self) -> int:
        """Return number of records"""
        return self.recordings.shape[0]

    def get_recording(self, index: int) -> np.ndarray:
        """Return recording `index` as an array of shape (recording_length, n_channels)."""
        return self.recordings[index]

    def get_channel(self, channel: str, recording: int | None = None) -> np.ndarray:
        """
        Return data for a single channel.

        If `recording` is None, returns every recording for this channel with
        shape (n_recordings, recording_length). Otherwise returns a 1D array
        for the given recording only.
        """
        ch_idx = self._channel_index(channel)
        if recording is None:
            return self.recordings[:, :, ch_idx]
        return self.recordings[recording, :, ch_idx]

    def _channel_index(self, channel: str) -> int:
        try:
            return self.channel_names.index(channel)
        except ValueError:
            raise KeyError(f"Unknown channel '{channel}'. Available: {self.channel_names}") from None

    # ------------------------------------------------------------------
    # Descriptive statistics
    # ------------------------------------------------------------------
    def describe(self) -> pd.DataFrame:
        """Per-channel descriptive statistics, pooled across all recordings."""
        return self._describe(None)

    def describe_recording(self, index: int | Sequence[int]) -> pd.DataFrame:
        """
        Per-channel descriptive statistics for one or more recordings.

        `index` may be a single recording index, returning the same shape as
        before, or a sequence of indexes, in which case the result gets an
        added 'recording' index level with one block of stats per recording.
        """
        if isinstance(index, (int, np.integer)):
            return self._describe(index)

        frames = {i: self._describe(i) for i in index}
        return pd.concat(frames, names=["recording"])

    def _describe(self, index: int | Sequence[int] | None) -> pd.DataFrame:

        if index is None:
            data=self.recordings
        elif isinstance(index, (int, np.integer)):
            data=self.get_recording(index)[np.newaxis, ...]
        
        rows = []
        for i, name in enumerate(self.channel_names):
            values = data[:, :, i].ravel()
            mean = np.mean(values)
            max_ = max(values)
            minimal_max = max_ if isinstance(index, (int, np.integer)) else self.__minimal_max(index, name)
            mean_max = (mean + minimal_max) / 2
            rows.append(
                {
                    "channel": name,
                    "mean": mean,
                    "std": np.std(values),
                    "min": np.min(values),
                    "max": max_,
                    "median": np.median(values),
                    "mean_max": mean_max,
                    "n_above_mean_max": int(np.sum(values > mean_max)),
                    "n_at_or_below_mean_max": int(np.sum(values <= mean_max)),
                }
            )
        return pd.DataFrame(rows).set_index("channel")
    def __minimal_max(self, index: Sequence[int] | None, channel: str):

        if index is None:
            index = np.arange(self.n_recordings)

        mini_max = np.inf
        for i in index:
            I_max = self.describe_recording(i)["max"][channel]
            if I_max < mini_max:
                mini_max = I_max
        return mini_max

    # ------------------------------------------------------------------
    # Spike discretization
    # ------------------------------------------------------------------
    def discretize(
        self,
        event_time_window: int,
        acceptance_threshold: pd.Series | float | None = None,
    ) -> np.ndarray:
        """
        Binary spike/no-spike series for every recording.

        Returns an array of shape (n_recordings, n_chunks, n_channels).
        """
        return np.stack(
            [
                self.discretize_recording(i, event_time_window, acceptance_threshold)
                for i in range(self.n_recordings)
            ]
        )

    def discretize_recording(
        self,
        index: int,
        event_time_window: int,
        acceptance_threshold: pd.Series | float | None = None,
    ) -> np.ndarray:
        """Binary spike/no-spike series for a single recording, shape (n_chunks, n_channels)."""
        return self._discretize(self.get_recording(index), event_time_window, acceptance_threshold)

    def _discretize(
        self,
        recording: np.ndarray,
        event_time_window: int,
        acceptance_threshold: pd.Series | float | None,
    ) -> np.ndarray:
        if acceptance_threshold is None:
            acceptance_threshold = self.global_mean_max

        n_chunks = self.recording_length // event_time_window
        if n_chunks == 0:
            raise ValueError(
                f"event_time_window={event_time_window} exceeds recording_length={self.recording_length}."
            )

        spikes = np.zeros((n_chunks, self.n_channels), dtype=int)
        for c, name in enumerate(self.channel_names):
            threshold = (
                acceptance_threshold[name]
                if isinstance(acceptance_threshold, pd.Series)
                else acceptance_threshold
            )
            channel_full = recording[:, c]
            channel = channel_full[: n_chunks * event_time_window]
            for k, chunk in enumerate(channel.reshape(n_chunks, event_time_window)):
                # Borrow a few trailing samples from the next chunk for spike detection only.
                chunk_end = (k + 1) * event_time_window
                lookahead = channel_full[chunk_end : chunk_end + SPIKE_LOOKAHEAD_OVERLAP]
                spikes[k, c] = self._has_spike(np.concatenate([chunk, lookahead]), threshold)
        return spikes

    @staticmethod
    def _has_spike(values: np.ndarray, threshold: float) -> bool:
        """
        Whether `values` contains t1 < t2, both >= threshold, whose midpoint
        sample exceeds both (i.e. the signal crosses threshold, peaks above
        both crossing points, then falls back).
        """
        n = len(values)
        for d in range(1, n // 2 + 1):
            m = np.arange(d, n - d)
            if m.size == 0:
                break
            left, right, mid = values[m - d], values[m + d], values[m]
            if np.any((left >= threshold) & (right >= threshold) & (mid > left) & (mid > right)):
                return True
        return False

    def opt_event_win(start=20):
        """Stimate an ideal events window lenght asumming"""
        
        return

    # ------------------------------------------------------------------
    # Visualization
    # ------------------------------------------------------------------
    def plot_recording(
        self,
        index: int = 0,
        ax: plt.Axes | None = None,
        zoom: tuple[int, int] | None = None,
        show_mean_max: bool = False,
        event_time_window: int | None = None,
        acceptance_threshold: pd.Series | float | None = None,
    ) -> plt.Axes:
        """
        Overlay all channels of a single recording on one time axis.

        `zoom` restricts the plot to a (start, end) sample-index interval of
        the recording; defaults to the full range. `show_mean_max` overlays a
        dashed horizontal line at each channel's mean_max threshold. If
        `event_time_window` is given, shades each chunk classified as a spike (see
        `discretize_recording`), using `acceptance_threshold` for the spike test.
        """
        recording = self.get_recording(index)
        time = self._time_axis()
        time, recording = self._apply_zoom(time, recording, zoom)

        spikes = (
            self.discretize_recording(index, event_time_window, acceptance_threshold)
            if event_time_window is not None
            else None
        )

        if ax is None:
            _, ax = plt.subplots(figsize=(10, 4))

        mean_max = self.describe_recording(index)["mean_max"] if show_mean_max else None
        for i, name in enumerate(self.channel_names):
            (line,) = ax.plot(time, recording[:, i], label=name)
            if show_mean_max:
                ax.axhline(mean_max[name], color=line.get_color(), linestyle="--", linewidth=1, alpha=0.7)
                ax.axhline(self.global_mean_max[name], color="black", linestyle="--", linewidth=1, alpha=0.7)
            if spikes is not None:
                self._shade_spike_regions(ax, spikes[:, i], event_time_window, zoom, line.get_color())
        ax.set_xlabel("Time (ms)")
        ax.set_ylabel("Potential")
        ax.set_title(f"{self.filename} - recording {index}")
        ax.legend()
        return ax

    def plot_channels(
        self,
        index: int = 0,
        zoom: tuple[int, int] | None = None,
        show_mean_max: bool = False,
        event_time_window: int | None = None,
        acceptance_threshold: pd.Series | float | None = None,
    ):
        """
        Plot each channel of a recording in its own stacked subplot.

        `zoom` restricts the plot to a (start, end) sample-index interval of
        the recording; defaults to the full range. `show_mean_max` overlays a
        dashed horizontal line at each channel's mean_max threshold. If
        `event_time_window` is given, shades each chunk classified as a spike (see
        `discretize_recording`), using `acceptance_threshold` for the spike test.
        """
        recording = self.get_recording(index)
        time = self._time_axis()
        time, recording = self._apply_zoom(time, recording, zoom)

        spikes = (
            self.discretize_recording(index, event_time_window, acceptance_threshold)
            if event_time_window is not None
            else None
        )

        fig, axes = plt.subplots(
            self.n_channels, 1, sharex=True, figsize=(10, 2.5 * self.n_channels)
        )
        axes = np.atleast_1d(axes)
        mean_max = self.describe_recording(index)["mean_max"] if show_mean_max else None
        for ax, name, i in zip(axes, self.channel_names, range(self.n_channels)):
            (line,) = ax.plot(time, recording[:, i])
            if show_mean_max:
                ax.axhline(mean_max[name], color="red", linestyle="--", linewidth=1, alpha=0.7)
                ax.axhline(self.global_mean_max[name], color="black", linestyle="--", linewidth=1, alpha=0.7)
            if spikes is not None:
                self._shade_spike_regions(ax, spikes[:, i], event_time_window, zoom, line.get_color())
            ax.set_ylabel(name)
        axes[-1].set_xlabel("Time (ms)")
        fig.suptitle(f"{self.filename} - recording {index}")
        fig.tight_layout()
        return fig, axes

    def _time_axis(self) -> np.ndarray:
        return np.arange(self.recording_length)

    @staticmethod
    def _apply_zoom(
        time: np.ndarray, recording: np.ndarray, zoom: tuple[int, int] | None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Slice `time`/`recording` to the (start, end) sample-index interval, if given."""
        if zoom is None:
            return time, recording
        start, end = zoom
        return time[start:end], recording[start:end]

    @staticmethod
    def _shade_spike_regions(
        ax: plt.Axes,
        channel_spikes: np.ndarray,
        event_time_window: int,
        zoom: tuple[int, int] | None,
        color: str,
    ) -> None:
        """Shade a background band over each chunk classified as a spike, cropped to `zoom`."""
        start, end = zoom if zoom is not None else (0, len(channel_spikes) * event_time_window)
        for k, is_spike in enumerate(channel_spikes):
            if not is_spike:
                continue
            chunk_start, chunk_end = k * event_time_window, (k + 1) * event_time_window
            if chunk_end <= start or chunk_start >= end:
                continue
            ax.axvspan(max(chunk_start, start), min(chunk_end, end), color=color, alpha=0.15, zorder=0)

    def __repr__(self) -> str:
        n_rec = self.n_recordings if self.recordings is not None else 0
        return (
            f"DataManager(filename={self.filename!r}, channels={self.channel_names}, "
            f"n_recordings={n_rec}, recording_length={self.recording_length})"
        )
