import sys
sys.path.insert(0, 'src')
from semabridge.api.services.project_runs_impl import _diff_models

# Test case 1: Two identical snapshots (all UNCHANGED)
print("Test 1 - Identical models (should all be UNCHANGED):")
left = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}, {'unique_name': 'Model2', 'label': 'Model 2'}]}
right = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}, {'unique_name': 'Model2', 'label': 'Model 2'}]}
result = _diff_models(left, right)
for r in result:
    print(f"  {r['name']}: {r['status']}")
print(f"Total models: {len(result)}")
print()

# Test case 2: Added model
print("Test 2 - Added model:")
left = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}]}
right = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}, {'unique_name': 'Model2', 'label': 'Model 2'}]}
result = _diff_models(left, right)
for r in result:
    print(f"  {r['name']}: {r['status']}")
print(f"Total models: {len(result)}")
print()

# Test case 3: Removed model
print("Test 3 - Removed model:")
left = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}, {'unique_name': 'Model2', 'label': 'Model 2'}]}
right = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}]}
result = _diff_models(left, right)
for r in result:
    print(f"  {r['name']}: {r['status']}")
print(f"Total models: {len(result)}")
print()

# Test case 4: Modified model
print("Test 4 - Modified model:")
left = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1', 'changed': True}]}
right = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}]}
result = _diff_models(left, right)
for r in result:
    print(f"  {r['name']}: {r['status']}")
print(f"Total models: {len(result)}")
print()

# Test case 5: Empty left state (all ADDED)
print("Test 5 - Empty left state (all ADDED):")
left = {}
right = {'datasets': [{'unique_name': 'Model1', 'label': 'Model 1'}]}
result = _diff_models(left, right)
for r in result:
    print(f"  {r['name']}: {r['status']}")
print(f"Total models: {len(result)}")
