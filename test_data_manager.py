import numpy as np
import pytest

from DataManager import DataManager


def test_mean_per_window_returns_each_channel_without_merging_recordings():
    manager = DataManager.__new__(DataManager)
    manager.channel_names = ["LP", "VD"]
    manager.recordings = np.array(
        [
            [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [4.0, 40.0], [5.0, 50.0]],
            [[10.0, 100.0], [20.0, 200.0], [30.0, 300.0], [40.0, 400.0], [50.0, 500.0]],
        ]
    )

    reduced = manager.mean_per_window(3)

    assert set(reduced) == {"LP", "VD"}
    assert reduced["LP"].shape == (2, 2)
    assert np.allclose(reduced["LP"], [[2.0, 4.5], [20.0, 45.0]])
    assert np.allclose(reduced["VD"], [[20.0, 45.0], [200.0, 450.0]])


@pytest.mark.parametrize("n", [0, -1, 1.5, True])
def test_mean_per_window_rejects_invalid_block_sizes(n):
    manager = DataManager.__new__(DataManager)
    manager.channel_names = ["LP"]
    manager.recordings = np.zeros((1, 5, 1))

    with pytest.raises(ValueError, match="positive integer"):
        manager.mean_per_window(n)
