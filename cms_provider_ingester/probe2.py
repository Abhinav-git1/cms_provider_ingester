import os, requests, collections, itertools, json

from dotenv import load_dotenv
load_dotenv()
DATASET_ID = os.getenv("CMS_DATASET_ID", "6fea9d79-0129-4e4c-b1b8-23cd86a4f435")
URL = f"https://data.cms.gov/data-api/v1/dataset/{DATASET_ID}/data"
params = {"limit": 1000, "offset": 0}
rows = requests.get(URL, params=params, timeout=60).json()
print("rows returned:", len(rows))

# Show first row keys (sorted)
if rows:
    keys = sorted(rows[0].keys())
    print("first row keys:", keys)

# Count presence of the fields we map
fields = [
    "Rndrng_NPI",
    "Rndrng_Prvdr_First_Name",
    "Rndrng_Prvdr_Last_Org_Name",
    "Rndrng_Prvdr_Type",
    "Rndrng_Prvdr_St1",
    "Rndrng_Prvdr_St2",
    "Rndrng_Prvdr_City",
    "Rndrng_Prvdr_State_Abrvtn",
    "Rndrng_Prvdr_Zip5",
]
counts = {f: 0 for f in fields}
for r in rows:
    for f in fields:
        v = r.get(f)
        if v not in (None, "", " "):
            counts[f] += 1
print("non-empty counts:", counts)

# Print one example row that has an NPI and an address
example = next(
    (r for r in rows if r.get("Rndrng_NPI") and (r.get("Rndrng_Prvdr_St1") or r.get("Rndrng_Prvdr_City"))),
    None
)
print("example row with NPI+address:", json.dumps(example, indent=2) if example else None)

