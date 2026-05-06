import json
from semabridge.sync.models import Model, Dataset, Metric
from semabridge.core.sync_modes import apply_sync_mode

# 1. Yeh hamara SOURCE hai (Fabric)
source_model = Model(
    name="Sales Model",
    datasets=[
        Dataset(name="Users", tables=["users_table"]),
        Dataset(name="Orders", tables=["orders_table"])
    ],
    metrics=[
        Metric(name="Total Sales", expression="SUM(sales)")
    ]
)

# 2. Yeh hamara TARGET hai (Snowflake)
# Dhyan de: Isme "Marketing" dataset aur "Custom Target Metric" extra hai!
target_model = Model(
    name="Sales Model",
    datasets=[
        Dataset(name="Users", tables=["users_table_old"]), # Old table
        Dataset(name="Marketing", tables=["marketing_campaigns"]) # EXTRA on target
    ],
    metrics=[
        Metric(name="Total Sales", expression="SUM(sales) * 0.9"), # Old logic
        Metric(name="Custom Target Metric", expression="SUM(custom)") # EXTRA on target
    ]
)

print("\n=== SOURCE DATASETS ===")
print([d.name for d in source_model.datasets])

print("\n=== TARGET DATASETS (Before Sync) ===")
print([d.name for d in target_model.datasets])

print("\n" + "="*50)
print(" 🚀 RUNNING COPY MODE ")
print("="*50)
copy_result = apply_sync_mode([source_model], [target_model], sync_mode="copy")
result_model = copy_result[0]
print("Datasets present after COPY:")
print([d.name for d in result_model.datasets])
print("-> (Notice 'Marketing' dataset is GONE. Target was completely replaced)")


print("\n" + "="*50)
print(" 🚀 RUNNING UPSERT MODE ")
print("="*50)
upsert_result = apply_sync_mode([source_model], [target_model], sync_mode="upsert")
result_model = upsert_result[0]
print("Datasets present after UPSERT:")
print([d.name for d in result_model.datasets])
print("Metrics present after UPSERT:")
print([m.name for m in result_model.metrics])
print("-> (Notice 'Marketing' dataset and 'Custom Target Metric' are PRESERVED! Source's 'Orders' was ADDED. Union successful!)")
print("\n")
