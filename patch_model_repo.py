import os

file_path = "c:/Users/Premasai/Documents/workspace/semabridge/src/semabridge/repository/model_repository.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

addition = """

    def list_all_snapshots(
        self, limit: int = 10000
    ) -> List[Snapshot]:
        \"\"\"List snapshots across all projects, newest first.\"\"\"
        with self._session() as session:
            rows = (
                session.execute(
                    select(SnapshotRow)
                    .order_by(SnapshotRow.timestamp.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._row_to_snapshot(r) for r in rows]
"""

target = "return [self._row_to_snapshot(r) for r in rows]"
if target in content and "def list_all_snapshots" not in content:
    pieces = content.split(target)
    # The first occurrence is in list_snapshots
    new_content = pieces[0] + target + addition + pieces[1]
    for i in range(2, len(pieces)):
        new_content += target + pieces[i]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("Added list_all_snapshots successfully")
else:
    print("Failed to find target string or already added")
