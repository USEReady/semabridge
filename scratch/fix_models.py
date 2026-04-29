import os

file_path = r"c:\Users\MANOJ\semabridge-working\src\semabridge\repository\orm\models.py"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "class Run(Base):" in line:
        new_lines.append(line)
        new_lines.append('    """Execution run lifecycle record.\n')
        new_lines.append('\n')
        new_lines.append('    Mirrors the ``runs`` table created by DuckDBManager DDL.\n')
        new_lines.append('    """\n')
        new_lines.append('\n')
        new_lines.append('    __tablename__ = "runs"\n')
        new_lines.append('    __table_args__ = (\n')
        new_lines.append('        Index("ix_runs_project", "project_id"),\n')
        new_lines.append('    )\n')
        new_lines.append('\n')
        new_lines.append('    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)\n')
        new_lines.append('    project_id: Mapped[str] = mapped_column(\n')
        new_lines.append('        ForeignKey("projects.project_id"), nullable=False\n')
        new_lines.append('    )\n')
        new_lines.append('    started_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)\n')
        new_lines.append('    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)\n')
        new_lines.append('    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")\n')
        new_lines.append('    final_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)\n')
        new_lines.append('    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)\n')
        new_lines.append('    target_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)\n')
        new_lines.append('    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)\n')
        new_lines.append('    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)\n')
        new_lines.append('    run_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)\n')
        new_lines.append('    sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")\n')
        new_lines.append('    before_src_snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)\n')
        new_lines.append('    restored_from_snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)\n')
        new_lines.append('    before_target_snapshot_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)\n')
        new_lines.append('    after_target_snapshot_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)\n')
        new_lines.append('\n')
        new_lines.append('    # Relationships\n')
        new_lines.append('    project: Mapped["Project"] = relationship(back_populates="runs")\n')
        new_lines.append('    source_artifacts: Mapped[List["SourceArtifact"]] = relationship(\n')
        new_lines.append('        back_populates="run", cascade="all, delete-orphan"\n')
        new_lines.append('    )\n')
        new_lines.append('\n')
        new_lines.append('    def __repr__(self) -> str:\n')
        new_lines.append('        return f"<Run(run_id={self.run_id!r}, status={self.status!r})>"\n')
        skip = True
        continue
    
    if skip:
        if "class " in line and "Run(Base)" not in line:
            skip = False
            new_lines.append(line)
        continue
    
    new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Successfully updated models.py")
