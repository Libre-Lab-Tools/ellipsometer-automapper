"""
ABSTRACT
--------
Acquisition/file coordinator used by the Measurement workspace.

The runner knows the CompleteEASE staging folder and one experiment's permanent
raw_data folder. For one requested coordinate it:
1. optionally moves the stage,
2. calls run_recipe(recipe, file_name),
3. verifies that a new readable TXT result was produced,
4. copies the new TXT and SE files into the experiment raw_data folder,
5. reparses the permanent raw_data folder into the current DataFrame.

It does not know about GUI widgets or plotting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

from completeease_common import CompleteEASECommunicationError
from data_parser import parse_completeease_txt, parse_txt_folder


@dataclass
class ExperimentPaths:
    root: Path
    raw_data: Path
    results: Path


@dataclass
class PointResult:
    success: bool
    dataframe: pd.DataFrame | None = None
    reason: str = ""


def create_experiment_folder(parent: str | Path, map_name: str = "") -> ExperimentPaths:
    """Create a unique timestamped experiment folder below the selected parent."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = map_name.strip()
    base_name = f"{stamp}_{clean_name}" if clean_name else stamp

    root = parent / base_name
    suffix = 1
    while root.exists():
        root = parent / f"{base_name}_{suffix:02d}"
        suffix += 1

    raw_data = root / "raw_data"
    results = root / "results"
    raw_data.mkdir(parents=True)
    results.mkdir(parents=True)

    return ExperimentPaths(root=root, raw_data=raw_data, results=results)


class MappingRunner:
    def __init__(self, completeease, staging_folder: str | Path) -> None:
        self.completeease = completeease
        self.staging_folder = Path(staging_folder)

    def acquire_point(
        self,
        recipe_name: str,
        file_name: str,
        experiment: ExperimentPaths,
        stage=None,
        x: int | None = None,
        y: int | None = None,
    ) -> PointResult:
        """Acquire one point and return the newly reparsed experiment table."""
        txt_source = self.staging_folder / f"{file_name}.txt"
        se_source = self.staging_folder / f"{file_name}.SE"

        txt_before = self._signature(txt_source)
        se_before = self._signature(se_source)

        if stage is not None:
            if x is None or y is None:
                raise ValueError("x and y are required when a stage is supplied.")
            stage.move("X", x)
            if hasattr(stage, "wait_until_idle"):
                stage.wait_until_idle()
            stage.move("Y", y)
            if hasattr(stage, "wait_until_idle"):
                stage.wait_until_idle()

        # Synchronous: return means CompleteEASE/simulator finished handling
        # this recipe request. Communication failures propagate as the shared
        # fatal error understood by the Measurement workspace.
        self.completeease.run_recipe(recipe_name, file_name)

        # Staging is never cleared. Instead, require the expected TXT to be new
        # or overwritten by this request so an old staging file cannot be used
        # after a failed measurement.
        txt_after = self._signature(txt_source)
        if txt_after is None or txt_after == txt_before:
            return PointResult(False, reason="No new TXT result was produced.")

        try:
            parameters = parse_completeease_txt(txt_source)
        except Exception as exc:
            return PointResult(False, reason=f"TXT result could not be read: {exc}")

        if not parameters:
            return PointResult(False, reason="TXT result contains no readable parameters.")

        shutil.copy2(txt_source, experiment.raw_data / txt_source.name)

        # The TXT is the success criterion. Copy SE when this request produced
        # one, but never copy an unchanged stale SE file.
        se_after = self._signature(se_source)
        if se_after is not None and se_after != se_before:
            shutil.copy2(se_source, experiment.raw_data / se_source.name)

        dataframe = parse_txt_folder(experiment.raw_data)
        dataframe.to_csv(experiment.root / "mapping_results.csv", index=False)
        return PointResult(True, dataframe=dataframe)

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        if not path.exists():
            return None
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size


class PointAcquisitionThread(QThread):
    """Run one potentially long stage + CompleteEASE point off the GUI thread."""

    finished_result = pyqtSignal(object)
    communication_error = pyqtSignal(str)
    unexpected_error = pyqtSignal(str)

    def __init__(
        self,
        runner: MappingRunner,
        recipe_name: str,
        file_name: str,
        experiment: ExperimentPaths,
        stage=None,
        x: int | None = None,
        y: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runner = runner
        self.recipe_name = recipe_name
        self.file_name = file_name
        self.experiment = experiment
        self.stage = stage
        self.x = x
        self.y = y

    def run(self) -> None:
        try:
            result = self.runner.acquire_point(
                recipe_name=self.recipe_name,
                file_name=self.file_name,
                experiment=self.experiment,
                stage=self.stage,
                x=self.x,
                y=self.y,
            )
        except CompleteEASECommunicationError as exc:
            self.communication_error.emit(str(exc))
        except Exception as exc:
            self.unexpected_error.emit(str(exc))
        else:
            self.finished_result.emit(result)
