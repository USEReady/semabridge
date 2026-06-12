import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import logging

from ..intermediate import OSIModel
from .models import SyncJob, SyncJobItem

logger = logging.getLogger(__name__)


class ArtifactExportService:
    """
    A service to export intermediate artifacts from a sync job for debugging and validation.
    """

    def __init__(self, root_output_dir: str = "output"):
        self.root_output_dir = Path(root_output_dir)
        self.sync_dir: Optional[Path] = None
        self.sync_metadata: Dict[str, Any] = {}
        self.trace_logger: Optional[logging.Logger] = None

    def initialize_sync_export(self, job: SyncJob):
        """
        Creates a unique directory for the sync job.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        source_type = job.config.source_type.value
        target_type = job.config.target_type.value
        dir_name = f"sync_{timestamp}_{source_type}_to_{target_type}"
        self.sync_dir = self.root_output_dir / dir_name
        self.sync_dir.mkdir(parents=True, exist_ok=True)

        self._setup_trace_logger()
        self.trace_logger.info("[ArtifactExport] Initialized sync export folder at %s", self.sync_dir)

        self.sync_metadata = {
            "sync_id": str(job.id),
            "source_type": source_type,
            "target_type": target_type,
            "start_time": datetime.utcnow().isoformat(),
            "status": "running",
        }

        # Create stage directories
        (self.sync_dir / "01_extraction").mkdir()
        (self.sync_dir / "02_osi").mkdir()
        (self.sync_dir / "03_sml").mkdir()
        (self.sync_dir / "04_target").mkdir()

    def _setup_trace_logger(self):
        if not self.sync_dir:
            return
        self.trace_logger = logging.getLogger(f"artifact_trace_{self.sync_dir.name}")
        self.trace_logger.setLevel(logging.INFO)
        handler = logging.FileHandler(self.sync_dir / "pipeline_trace.log")
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        handler.setFormatter(formatter)
        if not self.trace_logger.handlers:
            self.trace_logger.addHandler(handler)

    def _write_json(self, path: Path, data: Any, pretty: bool = False):
        if self.sync_dir:
            full_path = self.sync_dir / path
            with open(full_path, "w") as f:
                json.dump(data, f, indent=4 if pretty else None)
            if self.trace_logger:
                self.trace_logger.info("[ArtifactExport] Saved artifact to %s", path)

    def _write_text(self, path: Path, content: str):
        if self.sync_dir:
            full_path = self.sync_dir / path
            with open(full_path, "w") as f:
                f.write(content)
            if self.trace_logger:
                self.trace_logger.info("[ArtifactExport] Saved artifact to %s", path)

    def export_extraction_artifacts(self, item: SyncJobItem, extracted_data: Dict[str, Any]):
        """
        Exports artifacts from the extraction stage.
        """
        if not self.sync_dir:
            return

        extraction_dir = Path("01_extraction")
        
        if "model" in extracted_data:
            self._write_json(extraction_dir / "extracted_model.json", extracted_data["model"], pretty=True)
        
        summary = {
            "tables": len(extracted_data.get("tables", [])),
            "columns": sum(len(t.get("columns", [])) for t in extracted_data.get("tables", [])),
            "relationships": len(extracted_data.get("relationships", [])),
            "warnings": [],
            "errors": [],
        }
        self._write_json(extraction_dir / "extraction_summary.json", summary, pretty=True)
        if self.trace_logger:
            self.trace_logger.info("[ArtifactExport] Saved extraction artifact")

    def export_osi_artifacts(self, item: SyncJobItem, osi_model: OSIModel):
        """
        Exports artifacts from the OSI conversion stage.
        """
        if not self.sync_dir:
            return

        osi_dir = Path("02_osi")
        osi_data = osi_model.dict(by_alias=True)
        self._write_json(osi_dir / "osi.json", osi_data, pretty=True)

        summary = {
            "tables": len(osi_model.tables),
            "columns": sum(len(t.columns) for t in osi_model.tables),
            "relationships": len(osi_model.relationships),
            "measures": sum(len(t.measures) for t in osi_model.tables),
            "dimensions": 0, 
            "warnings": [],
            "errors": [],
        }
        self._write_json(osi_dir / "osi_summary.json", summary, pretty=True)
        if self.trace_logger:
            self.trace_logger.info("[ArtifactExport] Saved OSI artifact")

    def export_sml_artifacts(self, item: SyncJobItem, sml_model: Any):
        """
        Exports artifacts from the SML generation stage.
        """
        if not self.sync_dir:
            return
        
        sml_dir = Path("03_sml")
        sml_data = sml_model if isinstance(sml_model, dict) else {}
        self._write_json(sml_dir / "sml.json", sml_data, pretty=True)

        summary = {
            "tables": sml_data.get("table_count", 0),
            "columns": sml_data.get("column_count", 0),
            "relationships": sml_data.get("relationship_count", 0),
            "measures": sml_data.get("measure_count", 0),
            "dimensions": sml_data.get("dimension_count", 0),
            "warnings": [],
            "errors": [],
        }
        self._write_json(sml_dir / "sml_summary.json", summary, pretty=True)
        if self.trace_logger:
            self.trace_logger.info("[ArtifactExport] Saved SML artifact")

    def export_target_artifacts(self, item: SyncJobItem, target_artifacts: Dict[str, str]):
        """
        Exports the final generated target artifacts.
        """
        if not self.sync_dir:
            return

        target_dir = Path("04_target")
        
        for file_name, content in target_artifacts.items():
            if file_name.endswith(".sql"):
                self._write_text(target_dir / "generated.sql", content)
            elif file_name.endswith(".json"):
                try:
                    json_content = json.loads(content)
                    self._write_json(target_dir / "generated.json", json_content, pretty=True)
                except json.JSONDecodeError:
                    self._write_text(target_dir / "generated.json", content)

        summary = {
            "files_generated": list(target_artifacts.keys()),
            "warnings": [],
            "errors": [],
        }
        self._write_json(target_dir / "target_summary.json", summary, pretty=True)
        if self.trace_logger:
            self.trace_logger.info("[ArtifactExport] Saved target artifact")

    def finalize_sync(self, status: str, duration: float):
        """
        Writes the final metadata file for the sync.
        """
        if not self.sync_dir:
            return

        self.sync_metadata["end_time"] = datetime.utcnow().isoformat()
        self.sync_metadata["duration_seconds"] = round(duration, 2)
        self.sync_metadata["status"] = status
        self._write_json(Path("sync_metadata.json"), self.sync_metadata, pretty=True)
        if self.trace_logger:
            self.trace_logger.info("[ArtifactExport] Finalized sync export")

    def export_error_details(self, error: Exception):
        """
        Exports error details if a sync fails.
        """
        if not self.sync_dir:
            return

        error_details = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": str(error.__traceback__),
        }
        self._write_json(Path("error_details.json"), error_details, pretty=True)
        if self.trace_logger:
            self.trace_logger.error("[ArtifactExport] Exported error details: %s", str(error))
