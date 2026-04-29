import sys

def refactor_emitter(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    skip_until = -1
    
    for i, line in enumerate(lines):
        line_num = i + 1
        
        if line_num <= skip_until:
            continue
            
        # 1. Update Imports
        if 'from semabridge.connectors.schema_manager import SnowflakeSchemaManager' in line:
            new_lines.append(line)
            new_lines.append('from semabridge.connectors.measure_sync import MeasureSynchronizer\n')
            continue
            
        # 2. Update __init__
        if 'self.schema_manager = SnowflakeSchemaManager(' in line:
            new_lines.append(line)
            # Find the end of schema_manager init (it's multiline)
            j = i + 1
            while j < len(lines) and 'delegate=self,' not in lines[j]:
                new_lines.append(lines[j])
                j += 1
            if j < len(lines):
                new_lines.append(lines[j]) # delegate=self,
                new_lines.append(lines[j+1]) # )
                new_lines.append('        self.measure_synchronizer = MeasureSynchronizer(\n')
                new_lines.append('            config=self.config,\n')
                new_lines.append('            behavior=self.behavior,\n')
                new_lines.append('            identifier_sanitizer=self._id,\n')
                new_lines.append('            delegate=self,\n')
                new_lines.append('        )\n')
                skip_until = j + 2
            continue

        # 3. Remove generate_semantic_view_tiered (2235-2322)
        if line_num == 2235:
            skip_until = 2322
            continue
            
        # 4. Remove sync_measure_data (2328-2473)
        if line_num == 2328:
            skip_until = 2473
            continue

        # 5. Replace sync_all_measures (2475-2659)
        if line_num == 2475:
            new_lines.append('    def sync_all_measures(\n')
            new_lines.append('        self,\n')
            new_lines.append('        sml: SMLModel,\n')
            new_lines.append('        fabric_extractor,\n')
            new_lines.append('        dataset_id: str,\n')
            new_lines.append('        grain_dimensions: list[str] | None = None,\n')
            new_lines.append('    ) -> dict:\n')
            new_lines.append('        return self.measure_synchronizer.sync_all_measures(\n')
            new_lines.append('            sml, fabric_extractor, dataset_id, grain_dimensions\n')
            new_lines.append('        )\n\n')
            skip_until = 2659
            continue

        # 6. Replace sync_all_measures_from_osi (2661-2829)
        if line_num == 2661:
            new_lines.append('    def sync_all_measures_from_osi(\n')
            new_lines.append('        self,\n')
            new_lines.append('        osi: OSIModel,\n')
            new_lines.append('        fabric_extractor,\n')
            new_lines.append('        dataset_id: str,\n')
            new_lines.append('        grain_dimensions: list[str] | None = None,\n')
            new_lines.append('    ) -> dict:\n')
            new_lines.append('        return self.measure_synchronizer.sync_all_measures_from_osi(\n')
            new_lines.append('            osi, fabric_extractor, dataset_id, grain_dimensions\n')
            new_lines.append('        )\n')
            skip_until = 2829
            continue

        new_lines.append(line)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

if __name__ == "__main__":
    refactor_emitter(sys.argv[1])
